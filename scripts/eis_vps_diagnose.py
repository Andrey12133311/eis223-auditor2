#!/usr/bin/env python3
"""One read-only GetDocsIP request; Python standard library, no saved secrets."""
import argparse
import datetime as dt
import getpass
import json
import platform
import re
import socket
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
import warnings
import xml.etree.ElementTree as ET

ENDPOINT = "https://int.zakupki.gov.ru/eis-integration/services/getDocsIP"
HOST = "int.zakupki.gov.ru"
SOAP = "http://schemas.xmlsoap.org/soap/envelope/"
NS = "http://zakupki.gov.ru/fz44/get-docs-ip/ws"
SOAP_ACTION = "http://zakupki.gov.ru/fz44/queue/ws/get-docs-ip"
MAX_BODY = 1_000_000
AUTH_CODES = {"1", "2", "3", "4", "5", "24", "25", "26", "34", "35", "38"}


def redact(value, token=""):
    value = str(value)
    if token:
        value = value.replace(token, "[TOKEN]")
    # Error messages may echo another token. Never print UUID-shaped credentials.
    value = re.sub(r"\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b",
                   "[UUID]", value)
    return " ".join(value.split())[:700]


def build_request(reg_number):
    if not re.fullmatch(r"\d{11}", reg_number):
        raise ValueError("Номер закупки 223-ФЗ должен содержать 11 цифр")
    envelope = ET.Element("{%s}Envelope" % SOAP)
    ET.SubElement(envelope, "{%s}Header" % SOAP)
    body = ET.SubElement(envelope, "{%s}Body" % SOAP)
    request = ET.SubElement(body, "{%s}getDocsByReestrNumberRequest" % NS)
    index = ET.SubElement(request, "index")
    ET.SubElement(index, "id").text = str(uuid.uuid4())
    ET.SubElement(index, "createDateTime").text = dt.datetime.now(
        dt.timezone.utc).isoformat(timespec="seconds")
    ET.SubElement(index, "mode").text = "PROD"
    params = ET.SubElement(request, "selectionParams")
    ET.SubElement(params, "subsystemType").text = "RI223"
    ET.SubElement(params, "reestrNumber").text = reg_number
    return ET.tostring(envelope, encoding="utf-8", xml_declaration=True)


def local_name(element):
    return element.tag.rsplit("}", 1)[-1]


def classify(status, content, token=""):
    result = {"http": status, "xml": False}
    if len(content) > MAX_BODY:
        return dict(result, state="RESPONSE_TOO_LARGE")
    # No DTD/entities are needed for SOAP. Reject before XML parsing.
    if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
        return dict(result, state="UNEXPECTED_XML")
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        state = "HTTP_AUTH_ERROR" if status in (401, 403) else "HTTP_NON_XML"
        return dict(result, state=state)
    result["xml"] = True
    if root.tag != "{%s}Envelope" % SOAP:
        return dict(result, state="HTTP_AUTH_ERROR" if status in (401, 403)
                    else "UNEXPECTED_XML", root=redact(local_name(root), token))
    body = root.find("{%s}Body" % SOAP)
    if body is None:
        return dict(result, state="UNEXPECTED_XML")
    fault = body.find("{%s}Fault" % SOAP)
    if fault is not None:
        message = " ".join(fault.itertext())
        return dict(result, state="SOAP_FAULT", message=redact(message, token))
    response = body.find("{%s}getDocsByReestrNumberResponse" % NS)
    if response is None:
        return dict(result, state="UNEXPECTED_XML")
    result["response"] = "getDocsByReestrNumberResponse"
    data = response.find("dataInfo")
    if data is None:
        return dict(result, state="UNEXPECTED_XML")
    error = data.find("errorInfo")
    if error is not None:
        code = (error.findtext("code") or "").strip()
        state = "AUTH_ERROR" if code in AUTH_CODES else "EIS_ERROR"
        if code == "8":
            state = "PREPARING"
        elif code == "13":
            state = "RATE_LIMIT"
        return dict(result, state=state, code=redact(code, token),
                    message=redact(error.findtext("message") or "", token))
    if not 200 <= status < 300:
        return dict(result, state="HTTP_ERROR")
    if (data.findtext("noData") or "").strip() in ("true", "1"):
        return dict(result, state="NO_DOCUMENTS")
    archives = [(n.text or "").strip() for n in data.findall("archiveUrl")]
    if archives and all(urllib.parse.urlsplit(u).scheme in ("https", "http")
                        and urllib.parse.urlsplit(u).hostname for u in archives):
        return dict(result, state="ARCHIVE_LINKS", archive_count=len(archives))
    return dict(result, state="UNEXPECTED_XML")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward the token to a redirected host.


def send_probe(token, reg_number, timeout=30):
    request = urllib.request.Request(ENDPOINT, data=build_request(reg_number),
        headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": '"' + SOAP_ACTION + '"',
                 "individualPerson_token": token}, method="POST")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
        NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    try:
        response = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        response = error  # EIS errors can be returned as SOAP with HTTP 500.
    with response:
        content = response.read(MAX_BODY + 1)
        result = classify(response.status, content, token)
        result["bytes"] = len(content)
        return result


def transport_state(error):
    # URLError.reason is a nested exception; SSLError.reason is only a string.
    # Preserve SSLError itself so certificate failures remain TLS errors.
    reason = getattr(error, "reason", None)
    if not isinstance(reason, BaseException):
        reason = error
    if isinstance(reason, ssl.SSLError):
        return "TLS_ERROR"
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return "TIMEOUT"
    if isinstance(reason, socket.gaierror):
        return "DNS_ERROR"
    return "NETWORK_ERROR"


EXPLANATIONS = {
    "ARCHIVE_LINKS": "ЕИС вернула ссылки на архивы. Скачивание и содержимое ещё не проверены.",
    "NO_DOCUMENTS": "Получен ответ ЕИС: по этому номеру документы не найдены.",
    "PREPARING": "ЕИС сообщает: архив ещё формируется. Данные пока не получены.",
    "AUTH_ERROR": "ЕИС вернула ошибку токена, регистрации или прав доступа.",
    "HTTP_AUTH_ERROR": "HTTP отказ в доступе. Авторизация в сервисе не подтверждена.",
    "SOAP_FAULT": "Получен SOAP Fault. Успешное получение данных не подтверждено.",
    "HTTP_NON_XML": "Ответ не является XML SOAP; HTTP 200 сам по себе не означает подключение.",
    "UNEXPECTED_XML": "Ответ XML не соответствует ожидаемому ответу GetDocsIP.",
    "EIS_ERROR": "ЕИС вернула ошибку обработки запроса.",
    "RATE_LIMIT": "ЕИС ограничила частоту запросов. Не повторяйте запрос немедленно.",
    "TLS_ERROR": "Не установлено проверенное TLS-соединение; нужна диагностика сертификатов/TLS.",
    "TIMEOUT": "Истекло время ожидания соединения или ответа ЕИС.",
    "DNS_ERROR": "Сервер не смог определить IP-адрес сервиса ЕИС.",
    "NETWORK_ERROR": "Сетевая ошибка: запрос к сервису не завершён.",
}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Проверка прямого GetDocsIP ЕИС с VPS")
    parser.add_argument("--reg", default="32616440427", help="номер закупки 223-ФЗ")
    args = parser.parse_args(argv)
    try:
        build_request(args.reg)
    except ValueError as error:
        parser.error(str(error))
    print("ЕИС: проверка VPS, версия 1", flush=True)
    print("ОС:", platform.platform(), "Python:", platform.python_version(), flush=True)
    print("SSL:", ssl.OPENSSL_VERSION, flush=True)
    print("Сервис:", ENDPOINT, "Подсистема: RI223; закупка:", args.reg, flush=True)
    if not sys.stdin.isatty():
        print("Нужна интерактивная консоль: запустите python3 /tmp/eis-check.py отдельно.")
        return 2
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            token = getpass.getpass("Токен ПМД ЕИС (ввод скрыт): ").strip()
    except (EOFError, KeyboardInterrupt, getpass.GetPassWarning):
        print("\nСкрытый ввод недоступен или проверка отменена. Токен не отправлен.")
        return 2
    if not token or any(ord(c) < 33 or ord(c) > 126 for c in token):
        print("Токен пустой или содержит недопустимые символы.")
        return 2
    print("Проверяю TLS-соединение с ЕИС (до 12 секунд)...", flush=True)
    try:
        with socket.create_connection((HOST, 443), timeout=12) as raw:
            with ssl.create_default_context().wrap_socket(raw, server_hostname=HOST) as tls:
                print("TLS:", tls.version(), tls.cipher()[0], flush=True)
    except (OSError, urllib.error.URLError) as error:
        state = transport_state(error)
        print(json.dumps({"state": state, "detail": redact(error, token)}, ensure_ascii=False))
        print(EXPLANATIONS.get(state, state))
        return 2
    print("Отправляю один SOAP-запрос. Ожидание до 30 секунд...", flush=True)
    try:
        result = send_probe(token, args.reg)
    except (OSError, urllib.error.URLError) as error:
        result = {"state": transport_state(error), "detail": redact(error, token)}
    print(json.dumps(result, ensure_ascii=False), flush=True)
    print(EXPLANATIONS.get(result["state"], "Получение данных не подтверждено."), flush=True)
    return 0 if result["state"] in ("ARCHIVE_LINKS", "NO_DOCUMENTS") else 2


if __name__ == "__main__":
    sys.exit(main())
