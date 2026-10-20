#!/usr/bin/env python3
"""Outbound-only EIS SOAP -> site delivery worker. Python standard library only.

Credentials belong in a protected JSON file, never in arguments or logs.
`completed` means original files were delivered, not that legal review finished.
"""
from __future__ import annotations

import argparse
from concurrent.futures import Future, ThreadPoolExecutor
import contextlib
import hashlib
from html.parser import HTMLParser
import io
import json
import math
from email.utils import parsedate_to_datetime
import os
from pathlib import Path
import re
import signal
import sqlite3
import ssl
import stat
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone

VERSION = "1.4.1"
DEFAULT_SEARCH_INTERVAL_SECONDS = 60
DISCOVERY_PAGES = tuple(page for older in range(2, 11) for page in (1, older))
DEFAULT_INTERVAL_SECONDS = 30
MAX_ORIGINALS_PER_CYCLE = 48
MAX_JOBS_PER_CYCLE = 2
ENDPOINT = "https://int.zakupki.gov.ru/eis-integration/services/getDocsIP"
SOAP_NS = "http://schemas.xmlsoap.org/soap/envelope/"
API_NS = "http://zakupki.gov.ru/fz44/get-docs-ip/ws"
MAX_ZIP = 15 * 1024 * 1024
# Two originals at most are resident; keep this in sync with the server.
MAX_FILE = 64 * 1024 * 1024
MAX_XML = 2 * 1024 * 1024
REG = re.compile(r"[0-9]{11,20}\Z")
INN = re.compile(r"(?:[0-9]{10}|[0-9]{12})\Z")
UID = re.compile(r"[0-9A-F]{32}\Z")
STOP = False
SEARCH_URL = "https://zakupki.gov.ru/epz/order/extendedsearch/results.html?" + urllib.parse.urlencode({
    "sortBy": "UPDATE_DATE", "sortDirection": "false", "pageNumber": "1",
    "recordsPerPage": "_50", "showLotsInfoHidden": "false", "fz223": "on",
    "af": "on", "currencyIdGeneral": "-1"})


class RegistryLinks(HTMLParser):
    """Bounded discovery from official notice links, never buyer association."""
    VOID = frozenset(("area", "base", "br", "col", "embed", "hr", "img", "input",
                      "link", "meta", "param", "source", "track", "wbr"))
    ROW_CLASSES = frozenset(("search-registry-entry-block", "registry-entry",
                             "registry-entry__form", "search-registry-entry-block__form"))

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.numbers, self.controls, self.visible, self.links = [], [], [], []
        self.stack, self.anchor, self.row = [], None, None
        self.node_count = 0

    def add_number(self, value):
        if re.fullmatch(r"3[0-9]{10}", value) and value not in self.numbers and len(self.numbers) < 100:
            self.numbers.append(value)

    def notice_url(self, href):
        try:
            url = urllib.parse.urljoin(SEARCH_URL, href)
            official_url(url)
            parsed = urllib.parse.urlsplit(url)
            if not re.fullmatch(r"/(?:223/purchase/public/purchase/info|epz/order/notice/notice223)/(?:common-?info|documents)\.html", parsed.path):
                return None
            params = urllib.parse.parse_qs(parsed.query)
            if not any(len(params.get(key, [])) == 1 and re.fullmatch(r"[0-9]+", params[key][0])
                       for key in ("regNumber", "noticeId", "noticeInfoId")):
                return None
            return url
        except (WorkerError, ValueError):
            return None

    def handle_starttag(self, tag, attrs):
        self.node_count += 1
        if self.node_count > 100000 or len(self.stack) > 100:
            raise WorkerError("eis_search_markup_limit")
        attrs = dict(attrs)
        ignored = (bool(self.stack and self.stack[-1][1]) or tag in ("script", "style", "template")
                   or "hidden" in attrs or attrs.get("aria-hidden") == "true"
                   or bool(re.search(r"(?:display\s*:\s*none|visibility\s*:\s*hidden)", attrs.get("style", ""), re.I)))
        if tag == "input" and attrs.get("name") == "customerInn":
            self.controls.append(attrs.get("value", "").strip())
        if tag not in self.VOID:
            self.stack.append((tag, ignored))
        if ignored:
            return
        if self.row is None and (set(attrs.get("class", "").split()) & self.ROW_CLASSES):
            self.row = {"depth": len(self.stack), "text": [], "notice_link": False}
        if tag == "a":
            href = attrs.get("href", "")
            self.links.append(href)
            url = self.notice_url(href)
            if url:
                self.anchor = {"depth": len(self.stack), "text": [], "url": url}
                if self.row is not None:
                    self.row["notice_link"] = True
                values = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get("regNumber", [])
                if len(values) == 1:
                    self.add_number(values[0])

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_data(self, data):
        if self.stack and self.stack[-1][1]:
            return
        self.visible.append(data)
        if self.anchor is not None:
            self.anchor["text"].append(data)
        if self.row is not None:
            self.row["text"].append(data)

    def handle_endtag(self, tag):
        index = next((i for i in range(len(self.stack)-1, -1, -1) if self.stack[i][0] == tag), None)
        if index is None:
            return
        if self.anchor is not None and self.anchor["depth"] > index:
            text = " ".join(self.anchor["text"])
            if len(text) <= 300:
                found = set(re.findall(r"(?<![0-9])(3[0-9]{10})(?![0-9])", text))
                if len(found) == 1:
                    self.add_number(found.pop())
            self.anchor = None
        if self.row is not None and self.row["depth"] > index:
            text = " ".join(self.row["text"])
            found = set(re.findall(r"(?<![0-9])(3[0-9]{10})(?![0-9])", text))
            if self.row["notice_link"] and len(found) == 1:
                self.add_number(found.pop())
            self.row = None
        del self.stack[index:]


def search_parser(data):
    if len(data) > 6 * 1024 * 1024:
        raise WorkerError("search_response_size_limit", 1200)
    text = data.decode("utf-8", errors="replace")
    parser = RegistryLinks()
    parser.feed(text)
    parser.close()
    visible = " ".join(" ".join(parser.visible).split()).casefold()
    if (any(word in visible for word in ("captcha", "доступ временно ограничен",
                                       "подтвердите, что вы не робот", "verify you are human"))
            or not parser.numbers and "captcha" in text[:10000].casefold()):
        raise WorkerError("eis_search_browser_challenge", 1200)
    return parser


def discovery_numbers(data, allow_empty=False):
    parser = search_parser(data)
    if not parser.numbers:
        visible = " ".join(" ".join(parser.visible).split()).casefold()
        explicit_empty = any(text in visible for text in (
            "по вашему запросу ничего не найдено", "по заданным параметрам ничего не найдено",
            "по указанным параметрам ничего не найдено", "по вашему запросу не найдено записей"))
        if not allow_empty or not explicit_empty:
            raise WorkerError("eis_search_no_verified_links", 1200)
    return parser.numbers


def discovery_url(page):
    if type(page) is not int or not 1 <= page <= 10:
        raise WorkerError("invalid_discovery_page")
    parsed = urllib.parse.urlsplit(SEARCH_URL)
    query = urllib.parse.parse_qs(parsed.query)
    query["pageNumber"] = [str(page)]
    return urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(query, doseq=True)))


def discovery_source(source_url, page):
    parsed = urllib.parse.urlsplit(official_url(source_url))
    query = urllib.parse.parse_qs(parsed.query)
    if (parsed.hostname != "zakupki.gov.ru" or parsed.path != "/epz/order/extendedsearch/results.html"
            or query.get("pageNumber") != [str(page)] or query.get("fz223") != ["on"]
            or query.get("customerInn")):
        raise WorkerError("eis_discovery_page_mismatch")
    return source_url


def inn_search_url(inn, page):
    if not isinstance(inn, str) or not INN.fullmatch(inn) or type(page) is not int or not 1 <= page <= 10:
        raise WorkerError("invalid_inn_job")
    return "https://zakupki.gov.ru/epz/order/extendedsearch/results.html?" + urllib.parse.urlencode({
        "searchString": "", "morphology": "on", "openMode": "USE_DEFAULT_PARAMS",
        "pageNumber": page, "sortDirection": "false", "recordsPerPage": "_20",
        "showLotsInfoHidden": "false", "fz223": "on", "af": "on", "ca": "on",
        "pc": "on", "pa": "on", "customerInn": inn, "sortBy": "UPDATE_DATE",
        "currencyIdGeneral": "-1"})


def inn_candidates(data, inn, page, source_url):
    # Request controls confirm the page, not the buyer identity of its entries.
    inn_search_url(inn, page)
    parsed = urllib.parse.urlsplit(official_url(source_url))
    params = urllib.parse.parse_qs(parsed.query)
    if (parsed.hostname != "zakupki.gov.ru" or parsed.path != "/epz/order/extendedsearch/results.html"
            or params.get("customerInn") != [inn] or params.get("fz223") != ["on"]
            or params.get("pageNumber") != [str(page)]):
        raise WorkerError("eis_inn_search_url_mismatch")
    parser = search_parser(data)
    if any(value and value != inn for value in parser.controls):
        raise WorkerError("eis_inn_filter_mismatch")
    numbers = parser.numbers[:20]
    has_more = len(parser.numbers) >= 20
    for href in parser.links:
        try:
            link = urllib.parse.urlsplit(official_url(urllib.parse.urljoin(source_url, href)))
            query = urllib.parse.parse_qs(link.query)
            if (link.path == parsed.path and query.get("customerInn") == [inn]
                    and query.get("pageNumber") == [str(page + 1)]):
                has_more = True
        except (WorkerError, ValueError):
            continue
    if numbers:
        return numbers, has_more
    visible = " ".join(" ".join(parser.visible).split()).casefold()
    explicit_zero = any(text in visible for text in (
        "по вашему запросу ничего не найдено", "по заданным параметрам ничего не найдено",
        "по указанным параметрам ничего не найдено", "по вашему запросу не найдено записей"))
    if inn not in parser.controls or not explicit_zero or has_more:
        raise WorkerError("eis_inn_search_unrecognized_empty")
    return [], False


class WorkerError(Exception):
    """Only fixed, non-secret error codes are safe for logs and reports."""
    def __init__(self, code, retry_after=300):
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after


class ResponseSizeLimit(WorkerError):
    """Non-secret diagnostics; sizes are observations, never delivery receipts."""
    def __init__(self, kind, limit, observed):
        super().__init__("response_size_limit")
        self.context = {"kind": kind, "limit_bytes": limit, "observed_bytes": observed}


def global_backoff(code):
    return code in {"eis_http_403", "eis_http_429", "site_http_403", "site_http_429",
                    "eis_http_503", "site_http_503",
                    "eis_service_5", "eis_service_13", "eis_service_24",
                    "eis_service_34", "eis_service_35", "eis_service_38"}


def retry_after_delay(value, fallback):
    """Honor both HTTP forms; malformed/past values cannot shorten our pause."""
    if not isinstance(value, str) or len(value) > 128:
        return fallback
    value = value.strip()
    try:
        if value.isascii() and value.isdigit():
            seconds = int(value)
        else:
            parsed = parsedate_to_datetime(value)
            if parsed.tzinfo is None:
                return fallback
            seconds = math.ceil(parsed.timestamp() - time.time())
        return max(fallback, seconds)
    except (ValueError, TypeError, OverflowError):
        return fallback


def log(event, **fields):
    # Callers provide only counts, registry numbers, and fixed codes, never URLs.
    print(json.dumps({"time": datetime.now(timezone.utc).isoformat(),
                      "event": event, **fields}, ensure_ascii=False), flush=True)


def official_url(url):
    try:
        parsed = urllib.parse.urlsplit(url)
        host = (parsed.hostname or "").lower()
        valid = (parsed.scheme == "https" and parsed.port in (None, 443)
                 and not parsed.username and not parsed.password and not parsed.fragment
                 and (host == "zakupki.gov.ru" or host.endswith(".zakupki.gov.ru"))
                 and not any(ord(c) < 33 for c in url))
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise WorkerError("untrusted_eis_url")
    return url


class OfficialRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        official_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise WorkerError("site_redirect_refused")


def xml_root(data):
    upper = data.replace(b"\x00", b"").upper()
    if len(data) > MAX_XML or b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise WorkerError("unsafe_soap_xml")
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise WorkerError("invalid_soap_xml") from exc


def archive_urls(data):
    root = xml_root(data)
    if any(n.tag.rsplit("}", 1)[-1] == "Fault" for n in root.iter()):
        raise WorkerError("eis_soap_fault", 1200)
    if not any(n.tag.rsplit("}", 1)[-1] == "getDocsByReestrNumberResponse" for n in root.iter()):
        raise WorkerError("unexpected_soap_response")
    error = next((n for n in root.iter() if n.tag.rsplit("}", 1)[-1] == "errorInfo"), None)
    if error is not None:
        code = next(((n.text or "").strip() for n in error.iter()
                     if n.tag.rsplit("}", 1)[-1] == "code"), "unknown")
        code = code if re.fullmatch(r"[0-9]{1,6}", code) else "unknown"
        # Error messages may echo the token; expose only the official numeric code.
        raise WorkerError("eis_service_" + code, 1200 if code in ("5", "13", "24", "34", "35", "38") else 300)
    urls = []
    for n in root.iter():
        if n.tag.rsplit("}", 1)[-1] == "archiveUrl" and (n.text or "").strip():
            url = official_url(n.text.strip())
            if url not in urls:
                urls.append(url)
    if len(urls) > 20:
        raise WorkerError("too_many_eis_archives")
    if not urls and not any(n.tag.rsplit("}", 1)[-1] == "noData"
                            and (n.text or "").strip().lower() in ("true", "1")
                            for n in root.iter()):
        raise WorkerError("eis_incomplete_response")
    return urls


def soap_request(reg, token):
    if not REG.fullmatch(reg):
        raise WorkerError("invalid_registry_number")
    envelope = ET.Element(ET.QName(SOAP_NS, "Envelope"))
    header = ET.SubElement(envelope, ET.QName(SOAP_NS, "Header"))
    ET.SubElement(header, "individualPerson_token").text = token
    body = ET.SubElement(envelope, ET.QName(SOAP_NS, "Body"))
    request = ET.SubElement(body, ET.QName(API_NS, "getDocsByReestrNumberRequest"))
    index = ET.SubElement(request, "index")
    for name, value in (("id", str(uuid.uuid4())),
                        ("createDateTime", datetime.now(timezone.utc).isoformat(timespec="seconds")),
                        ("mode", "PROD")):
        ET.SubElement(index, name).text = value
    params = ET.SubElement(request, "selectionParams")
    for name, value in (("subsystemType", "RI223"), ("reestrNumber", reg)):
        ET.SubElement(params, name).text = value
    return ET.tostring(envelope, encoding="utf-8", xml_declaration=True)


def verify_zip(data):
    if len(data) > MAX_ZIP:
        raise WorkerError("archive_exceeds_15_mib")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            items = archive.infolist()
            if not items or len(items) > 100 or sum(i.file_size for i in items) > 50 * 1024 * 1024:
                raise WorkerError("archive_expansion_limit")
            for item in items:
                if (item.flag_bits & 1 or item.file_size > MAX_ZIP or item.is_dir()
                        or "/" in item.filename or "\\" in item.filename or ".." in item.filename
                        or not item.filename.lower().endswith(".xml")):
                    raise WorkerError("unsafe_archive_member")
            # Validate CRC without extracting any path or loading all members at once.
            if archive.testzip() is not None:
                raise WorkerError("archive_crc_error")
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError) as exc:
        raise WorkerError("invalid_archive") from exc


def validate_token(value, name):
    """Normalize a pasted opaque token and fail without disclosing any value."""
    if name not in ("eis_token", "site_token"):
        raise WorkerError("invalid_token_field")
    if value is None or isinstance(value, str) and not value.strip():
        raise WorkerError(name + "_empty")
    if not isinstance(value, str):
        raise WorkerError(name + "_invalid_characters")
    value = value.strip()
    if any(not 0x21 <= ord(char) <= 0x7E for char in value):
        raise WorkerError(name + "_invalid_characters")
    if value.upper() in ("EIS223_UPLOAD_TOKEN", "EIS_TOKEN"):
        raise WorkerError(name + "_variable_name")
    return value


def load_config(path):
    p = Path(path)
    st = p.stat()
    if not stat.S_ISREG(st.st_mode) or st.st_mode & 0o007 or st.st_mode & 0o020:
        raise WorkerError("credentials_permissions_unsafe", 1200)
    if st.st_size > 65536:
        raise WorkerError("credentials_file_too_large")
    try:
        config = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise WorkerError("credentials_invalid_json") from exc
    for name in ("eis_token", "site_token"):
        config[name] = validate_token(config.get(name), name)
    url = str(config.get("site_url", "")).rstrip("/")
    try:
        u = urllib.parse.urlsplit(url)
        if not (u.scheme == "https" and u.hostname and not u.username and not u.password
                and not u.path and not u.query and not u.fragment and u.port in (None, 443)):
            raise ValueError()
    except ValueError as exc:
        raise WorkerError("site_url_must_be_https_origin") from exc
    config["site_url"] = url
    config["interval_seconds"] = max(DEFAULT_INTERVAL_SECONDS, int(config.get("interval_seconds", DEFAULT_INTERVAL_SECONDS)))
    config["search_interval_seconds"] = min(3600, max(30, int(config.get("search_interval_seconds", DEFAULT_SEARCH_INTERVAL_SECONDS))))
    config["timeout_seconds"] = min(60, max(10, int(config.get("timeout_seconds", 30))))
    return config


class Transport:
    def __init__(self, config):
        self.config = config
        eis_ssl = ssl.create_default_context()
        eis_ssl.load_verify_locations(cafile=config["ca_file"])
        self.eis_opener = urllib.request.build_opener(OfficialRedirect(),
            urllib.request.HTTPSHandler(context=eis_ssl))
        self.site_opener = urllib.request.build_opener(NoRedirect(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()))
        self.last_file_at = 0.0

    def request(self, url, method="GET", data=None, *, eis=False, headers=None, limit=MAX_XML, send_token=True, response_kind=None):
        if eis:
            official_url(url)
        elif not url.startswith(self.config["site_url"] + "/"):
            raise WorkerError("unexpected_site_url")
        # Validate again even when callers inject a config without load_config().
        # Unicode is valid in XML, but HTTP token headers must stay printable ASCII.
        token_name = "eis_token" if eis and send_token else "site_token" if not eis else None
        token = validate_token(self.config.get(token_name), token_name) if token_name else None
        hdr = {"User-Agent": "eis223-vps-worker/" + VERSION, **(headers or {})}
        if eis and send_token:
            hdr["individualPerson_token"] = token
        elif not eis:
            hdr["x-eis223-upload-token"] = token
        request = urllib.request.Request(url, data=data, method=method, headers=hdr)
        opener = self.eis_opener if eis else self.site_opener
        try:
            with opener.open(request, timeout=self.config["timeout_seconds"]) as response:
                self.last_response_url = response.geturl()
                kind = response_kind or ("eis_soap" if eis else "site_response")
                advertised = response.headers.get("Content-Length", "")
                if advertised.isdigit() and int(advertised) > limit:
                    raise ResponseSizeLimit(kind, limit, int(advertised))
                deadline = time.monotonic() + self.config["timeout_seconds"] * 2
                # BytesIO avoids retaining a chunk list plus a second full copy.
                output, count = io.BytesIO(), 0
                while True:
                    chunk = response.read(min(65536, limit + 1 - count))
                    if not chunk:
                        break
                    count += len(chunk)
                    if count > limit:
                        raise ResponseSizeLimit(kind, limit, count)
                    output.write(chunk)
                    if time.monotonic() > deadline:
                        raise WorkerError("response_deadline")
                if advertised.isdigit() and count != int(advertised):
                    raise WorkerError("response_length_mismatch")
                return output.getvalue()
        except urllib.error.HTTPError as exc:
            retry_header = (exc.headers.get("Retry-After") or exc.headers.get("retry-after")) if exc.headers else None
            scope = (exc.headers.get("X-EIS223-Retry-Scope") or exc.headers.get("x-eis223-retry-scope")) if exc.headers else None
            # Only the authenticated site's exact discovery receiver may scope
            # local queue backpressure. EIS/global limits never use this escape.
            if (not eis and method == "POST" and url == self.config["site_url"] + "/api/eis-worker/discovered"
                    and exc.code in (429, 503) and scope == "discovery"):
                raise WorkerError("site_discovery_backpressure", retry_after_delay(retry_header, 30)) from None
            retry = retry_after_delay(retry_header, 1200 if exc.code in (403, 429) else 300)
            # Never emit server response, request URL, or exception repr (tokens may be there).
            raise WorkerError(("eis" if eis else "site") + "_http_" + str(exc.code), retry) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise WorkerError(("eis" if eis else "site") + "_connection_error") from None

    def site(self, path, data=None, method=None):
        method = method or ("POST" if data is not None else "GET")
        body = json.dumps(data, separators=(",", ":")).encode() if data is not None else None
        raw = self.request(self.config["site_url"] + path, method, body,
                           headers={"Content-Type": "application/json"})
        if not raw:
            return {}
        try:
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except (ValueError, UnicodeError) as exc:
            raise WorkerError("site_invalid_json") from exc

    def upload(self, path, data, content_type):
        raw = self.request(self.config["site_url"] + path, "POST", data,
                           headers={"Content-Type": content_type})
        try:
            result = json.loads(raw)
            if result.get("accepted") is not True:
                raise ValueError()
            return result
        except (ValueError, AttributeError, UnicodeError) as exc:
            raise WorkerError("site_upload_not_accepted") from exc

    def soap(self, reg):
        raw = self.request(ENDPOINT, "POST", soap_request(reg, self.config["eis_token"]),
                           eis=True, headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": '""'})
        return archive_urls(raw)

    def download(self, url, limit, *, cancel=None):
        # Exactly one download thread uses this limiter. A speculative file
        # waiting for its slot must not start after the active upload failed.
        while True:
            if cancel is not None and (cancel.is_set() or STOP):
                raise WorkerError("download_cancelled")
            wait = 2.5 - (time.monotonic() - self.last_file_at)
            if wait <= 0:
                break
            if cancel is None:
                time.sleep(wait)
            else:
                cancel.wait(wait)
        self.last_file_at = time.monotonic()
        return self.request(url, eis=True, limit=limit,
                            response_kind="eis_archive" if limit == MAX_ZIP else "eis_original")


class State:
    def __init__(self, directory):
        self.root = Path(directory)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.db = sqlite3.connect(self.root / "state.sqlite3", timeout=15)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS reports (job_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS downloads (cache_key TEXT PRIMARY KEY, sha256 TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS deliveries (reg TEXT, kind TEXT, identity TEXT, sha256 TEXT,
          delivered_at REAL, PRIMARY KEY(reg,kind,identity));
        """)
        self.db.commit()
        os.chmod(self.root / "state.sqlite3", 0o600)
        self.worker_id = self.get("worker_id") or str(uuid.uuid4())
        self.set("worker_id", self.worker_id)
        self.cleanup_cache()

    def get(self, name, default=None):
        row = self.db.execute("SELECT value FROM settings WHERE name=?", (name,)).fetchone()
        return row[0] if row else default

    def set(self, name, value):
        self.db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (name, str(value)))
        self.db.commit()

    def defer_requests(self, seconds, *, site=False):
        until = time.time() + seconds
        self.set("backoff_until", max(float(self.get("backoff_until", "0")), until))
        if site:
            self.set("site_backoff_until", max(float(self.get("site_backoff_until", "0")), until))

    def take_search_slot(self, interval=DEFAULT_SEARCH_INTERVAL_SECONDS, kind="general"):
        now = time.time()
        allowed_at = float(self.get("search_not_before", "0"))
        if now < allowed_at:
            raise WorkerError("eis_search_cooldown", max(1, int(allowed_at - now + 1)))
        # Commit before the request so crashes/restarts cannot duplicate a page fetch.
        self.db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", ("search_not_before", str(now + interval)))
        self.db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", ("last_search_kind", kind))
        self.db.commit()

    def discovery_cursor(self):
        return int(self.get("discovery_cursor", "0")) % len(DISCOVERY_PAGES)

    def pending_discovery(self):
        value = self.get("pending_discovery")
        return json.loads(value) if value else None

    def save_discovery(self, payload, next_cursor):
        self.set("pending_discovery", json.dumps({"payload": payload, "next_cursor": next_cursor}))

    def acknowledge_discovery(self, next_cursor):
        # Cursor and removal commit together; a lost acknowledgement replays
        # the same page, whose registry numbers the server deduplicates.
        self.db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", ("discovery_cursor", str(next_cursor)))
        self.db.execute("DELETE FROM settings WHERE name='pending_discovery'")
        self.db.commit()

    def defer_search(self, seconds):
        self.set("search_not_before", max(float(self.get("search_not_before", "0")),
                                         time.time() + max(1, seconds)))

    def pending_reports(self):
        return [(row[0], json.loads(row[1])) for row in self.db.execute("SELECT job_id,payload FROM reports")]

    def save_report(self, job_id, report):
        self.db.execute("INSERT OR REPLACE INTO reports VALUES (?,?)", (str(job_id), json.dumps(report)))
        self.db.commit()

    def remove_report(self, job_id):
        self.db.execute("DELETE FROM reports WHERE job_id=?", (str(job_id),))
        self.db.commit()

    def cached(self, cache_key):
        row = self.db.execute("SELECT sha256 FROM downloads WHERE cache_key=?", (cache_key,)).fetchone()
        path = self.root / (cache_key + ".part")
        if row and path.is_file() and path.stat().st_size <= MAX_FILE:
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() == row[0]:
                return data
        return None

    def cache(self, cache_key, data):
        self.cleanup_cache()
        path = self.root / (cache_key + ".part")
        tmp = self.root / (cache_key + ".tmp")
        with open(tmp, "wb") as handle:
            os.chmod(tmp, 0o600)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        self.db.execute("INSERT OR REPLACE INTO downloads VALUES (?,?)",
                        (cache_key, hashlib.sha256(data).hexdigest()))
        self.db.commit()

    def discard_download(self, cache_key):
        if not cache_key:
            return
        self.db.execute("DELETE FROM downloads WHERE cache_key=?", (cache_key,))
        self.db.commit()
        (self.root / (cache_key + ".part")).unlink(missing_ok=True)

    def delivered(self, reg, kind, identity, data, cache_key):
        self.db.execute("INSERT OR REPLACE INTO deliveries VALUES (?,?,?,?,?)",
                        (reg, kind, identity, hashlib.sha256(data).hexdigest(), time.time()))
        self.db.execute("DELETE FROM downloads WHERE cache_key=?", (cache_key,))
        self.db.commit()
        (self.root / (cache_key + ".part")).unlink(missing_ok=True)

    def cleanup_cache(self):
        paths = sorted(self.root.glob("*.part"), key=lambda p: p.stat().st_mtime)
        total = sum(p.stat().st_size for p in paths)
        for path in paths:
            if total <= 250 * 1024 * 1024 and path.stat().st_mtime >= time.time() - 7 * 86400:
                continue
            total -= path.stat().st_size
            path.unlink(missing_ok=True)
            self.db.execute("DELETE FROM downloads WHERE cache_key=?", (path.stem,))
        self.db.commit()


def cache_key(reg, identity, url):
    return hashlib.sha256((reg + "\0" + identity + "\0" + url).encode()).hexdigest()


class OriginalPipeline:
    """One look-ahead download; SQLite and all uploads stay on the main thread.

    The current original and at most one prefetched original are resident.
    Download concurrency stays one; the shared cycle budget bounds all attempts.
    On failure, a file already in flight is cached for the ordinary retry.
    """
    def __init__(self, worker, reg, documents):
        self.worker, self.reg = worker, reg
        self.documents = iter(documents)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="eis-original")
        self.cancel = threading.Event()
        self.pending = None

    def prefetch(self):
        if self.pending is not None or STOP or self.worker.files_this_cycle >= MAX_ORIGINALS_PER_CYCLE:
            return
        for item in self.documents:
            if item.get("received") is True:
                continue
            self.worker.files_this_cycle += 1
            uid = str(item.get("uid", ""))
            future = Future()
            transfer = {"uid": uid, "key": None, "future": future, "cached": True}
            self.pending = transfer
            try:
                if not UID.fullmatch(uid):
                    raise WorkerError("invalid_manifest_uid")
                url = official_url(str(item.get("url", "")))
                key = cache_key(self.reg, uid, url)
                transfer["key"] = key
                data = self.worker.state.cached(key)
                if data is not None:
                    future.set_result(data)
                else:
                    transfer["cached"] = False
                    transfer["future"] = self.executor.submit(
                        self.worker.http.download, url, MAX_FILE, cancel=self.cancel)
            except WorkerError as exc:
                future.set_exception(exc)
            return

    def take(self):
        self.prefetch()
        transfer, self.pending = self.pending, None
        return transfer

    def result(self, transfer):
        data = transfer["future"].result()
        if not transfer["cached"]:
            self.worker.state.cache(transfer["key"], data)
            transfer["cached"] = True
        return transfer["key"], data

    def close(self):
        active_error = sys.exc_info()[1]
        self.cancel.set()
        try:
            if self.pending and not self.pending["future"].cancel():
                # Keep an already-downloaded original even when its predecessor
                # failed to upload. Cancelled/rate-limited downloads have no bytes.
                try:
                    self.result(self.pending)
                except WorkerError as exc:
                    # A concurrent site failure must not hide an EIS 403/429
                    # from the prefetched request or shorten its global pause.
                    if global_backoff(exc.code):
                        retry = max(exc.retry_after,
                                    active_error.retry_after if isinstance(active_error, WorkerError) else 0)
                        until = max(float(self.worker.state.get("backoff_until", "0")),
                                    time.time() + retry)
                        self.worker.state.set("backoff_until", until)
                        if active_error is None or isinstance(active_error, WorkerError):
                            raise WorkerError(exc.code, retry) from None
        finally:
            self.executor.shutdown(wait=True, cancel_futures=True)


class Worker:
    def __init__(self, config, state, transport=None):
        self.config, self.state = config, state
        self.search_interval = min(3600, max(30, int(config.get("search_interval_seconds", DEFAULT_SEARCH_INTERVAL_SECONDS))))
        self.http = transport or Transport(config)
        self.worker_id = state.worker_id
        self.files_this_cycle = 0
        self.search_during_documents = False
        self.last_heartbeat = 0.0
        self.discovery_status = state.get("discovery_status", "pending")
        self.discovery_error = state.get("discovery_error", "")

    def heartbeat(self, job=None, force=False):
        if float(self.state.get("site_backoff_until", "0")) > time.time():
            return
        if not force and time.monotonic() - self.last_heartbeat < 60:
            return
        body = {"worker_id": self.worker_id, "version": VERSION,
                "status": "working" if job else "idle", "capabilities": ["inn_search"],
                "discovery_status": self.discovery_status, "discovery_error": self.discovery_error,
                "queue_interval_seconds": self.config.get("interval_seconds", DEFAULT_INTERVAL_SECONDS),
                "search_interval_seconds": self.search_interval,
                "queue_backoff_seconds": max(0, math.ceil(float(self.state.get("backoff_until", "0")) - time.time())),
                "search_backoff_seconds": max(0, math.ceil(max(float(self.state.get("search_not_before", "0")),
                    float(self.state.get("discovery_backoff_until", "0"))) - time.time())),
                "discovery_pending": self.state.pending_discovery() is not None,
                "discovery_page": DISCOVERY_PAGES[self.state.discovery_cursor()]}
        if job:
            body.update(current_job_id=job["id"], lease_token=job["lease_token"])
        try:
            self.http.site("/api/eis-worker/heartbeat", body)
        except WorkerError as exc:
            if global_backoff(exc.code):
                self.state.defer_requests(exc.retry_after, site=exc.code.startswith("site_"))
            raise
        self.last_heartbeat = time.monotonic()

    def retry_discovery(self):
        pending = self.state.pending_discovery()
        if pending is None:
            return True
        now = time.time()
        if max(float(self.state.get("site_backoff_until", "0")),
               float(self.state.get("discovery_delivery_not_before", "0"))) > now:
            return False
        try:
            response = self.http.site("/api/eis-worker/discovered", pending["payload"])
            if response.get("accepted") is not True:
                raise WorkerError("site_discovery_not_accepted")
            self.state.acknowledge_discovery(pending["next_cursor"])
            self.discovery_status, self.discovery_error = "ok", ""
            log("discovery", found=len(pending["payload"]["reg_numbers"]))
            accepted = True
        except WorkerError as exc:
            self.discovery_status, self.discovery_error = "pending_delivery", exc.code
            # Delivery errors cannot consume another EIS slot or discard the page.
            # Source cooldown and receiving-site retry deadline are independent.
            self.state.set("discovery_delivery_not_before", time.time() + exc.retry_after)
            if global_backoff(exc.code):
                self.state.defer_requests(exc.retry_after, site=exc.code.startswith("site_"))
            log("discovery_delivery_error", code=exc.code)
            accepted = False
        self.state.set("discovery_status", self.discovery_status)
        self.state.set("discovery_error", self.discovery_error)
        return accepted

    def search_ready(self):
        return (not STOP and self.state.pending_discovery() is None
                and max(float(self.state.get("search_not_before", "0")),
                        float(self.state.get("discovery_backoff_until", "0")),
                        float(self.state.get("site_backoff_until", "0")),
                        float(self.state.get("backoff_until", "0"))) <= time.time())

    def discover(self):
        # Deliver old results even while the source itself is cooling down.
        if self.state.pending_discovery() is not None:
            self.retry_discovery()
            return
        if not self.search_ready() or self.state.get("inn_opportunity_pending", "0") == "1":
            return
        try:
            self.state.take_search_slot(self.search_interval, "general")
            # Reserve the next free public slot for a capable queue claim.
            # Otherwise long document jobs could consume every slot here and
            # continuously exclude queued INN work from the following claims.
            self.state.set("inn_opportunity_pending", "1")
            cursor = self.state.discovery_cursor()
            page = DISCOVERY_PAGES[cursor]
            url = discovery_url(page)
            raw = self.http.request(url, eis=True, limit=6 * 1024 * 1024, send_token=False, response_kind="eis_search")
            source = discovery_source(getattr(self.http, "last_response_url", url), page)
            numbers = discovery_numbers(raw, allow_empty=True)
            next_cursor = (cursor + 1) % len(DISCOVERY_PAGES) if numbers else 0
            self.state.save_discovery({"worker_id": self.worker_id, "reg_numbers": numbers,
                                       "source_url": source}, next_cursor)
        except WorkerError as exc:
            self.discovery_status = "blocked" if exc.retry_after >= 1200 else "error"
            self.discovery_error = exc.code
            self.state.defer_search(exc.retry_after)
            self.state.set("discovery_backoff_until", max(float(self.state.get("discovery_backoff_until", "0")),
                                                           time.time() + exc.retry_after))
            self.state.set("discovery_status", self.discovery_status)
            self.state.set("discovery_error", self.discovery_error)
            log("discovery_error", code=exc.code)
            return
        self.retry_discovery()

    def discovery_checkpoint(self, pipeline=None):
        if not self.search_during_documents:
            return
        pending = self.state.pending_discovery()
        if (pending is None and (not self.search_ready() or self.state.get("inn_opportunity_pending", "0") == "1")) or (pending is not None
                and float(self.state.get("discovery_delivery_not_before", "0")) > time.time()):
            return
        # Do not overlap public search with the look-ahead EIS download.
        # The completed file remains cached for the next pipeline iteration.
        if pipeline is not None and pipeline.pending is not None:
            pipeline.result(pipeline.pending)
        self.discover()
        site_pause = float(self.state.get("site_backoff_until", "0")) - time.time()
        if site_pause > 0:
            raise WorkerError("site_backoff_active", math.ceil(site_pause))

    def retry_reports(self):
        for job_id, payload in self.state.pending_reports():
            try:
                kind = "inn" if payload.get("kind") == "inn" else "reg"
                collection = "inn-jobs" if kind == "inn" else "jobs"
                result = self.http.site("/api/eis-worker/" + collection + "/" + urllib.parse.quote(job_id, safe="") + "/report", payload)
                if result.get("received") is not True:
                    raise WorkerError("site_report_not_accepted")
            except WorkerError as exc:
                if global_backoff(exc.code):
                    self.state.defer_requests(exc.retry_after, site=exc.code.startswith("site_"))
                if exc.code in ("site_http_409", "site_http_410", "site_http_404"):
                    # A new lease will consult server receipts and safely resume delivery.
                    self.state.remove_report(job_id)
                    log("report_lease_expired", code=exc.code)
                    continue
                raise
            self.state.remove_report(job_id)
            accepted_status = result.get("status")
            if accepted_status not in ("done", "queued", "partial", "error"):
                accepted_status = "unknown"
            if payload.get("kind") == "inn":
                log("inn_report_ack", inn=payload["inn"], page=payload["page"], status=accepted_status,
                    search_complete=result.get("search_complete") is True,
                    candidate_count=result.get("candidate_count", 0),
                    pagination_limited=result.get("pagination_limited") is True, audit_complete=False)
            else:
                log("report_ack", reg_number=payload["reg_number"], status=accepted_status,
                    download_complete=result.get("download_complete") is True,
                    audit_complete=False)

    def get_file(self, reg, identity, url, limit):
        key = cache_key(reg, identity, url)
        data = self.state.cached(key)
        if data is None:
            data = self.http.download(official_url(url), limit)
            self.state.cache(key, data)
        return key, data

    def process_inn(self, job):
        inn, page = str(job.get("target") or job.get("inn") or ""), job.get("page")
        url = inn_search_url(inn, page)
        if not job.get("id") or not job.get("lease_token"):
            raise WorkerError("invalid_inn_job")
        report = {"kind": "inn", "worker_id": self.worker_id, "lease_token": job["lease_token"],
                  "inn": inn, "page": page, "status": "error", "reg_numbers": [],
                  "has_more": False, "source_url": "", "error_code": "", "retry_after_seconds": 300}
        try:
            self.heartbeat(job, force=True)
            self.state.take_search_slot(self.search_interval, "inn")
            raw = self.http.request(url, eis=True, limit=6 * 1024 * 1024, send_token=False, response_kind="eis_search")
            source_url = getattr(self.http, "last_response_url", url)
            numbers, more = inn_candidates(raw, inn, page, source_url)
            report.update(status="completed" if numbers else "no_data", reg_numbers=numbers,
                          has_more=more, source_url=source_url, retry_after_seconds=0)
            self.discovery_status, self.discovery_error = "ok", ""
        except WorkerError as exc:
            report.update(error_code=exc.code, retry_after_seconds=min(86400, exc.retry_after))
            if exc.code != "eis_search_cooldown":
                self.state.defer_search(exc.retry_after)
                self.discovery_status = "blocked" if exc.retry_after >= 1200 else "error"
                self.discovery_error = exc.code
        return report

    def process(self, job):
        reg = str(job.get("reg_number", ""))
        if not REG.fullmatch(reg) or not job.get("id") or not job.get("lease_token"):
            raise WorkerError("invalid_job")
        report = {"worker_id": self.worker_id, "lease_token": job["lease_token"],
                  "reg_number": reg, "status": "error", "archives_uploaded": 0,
                  "originals_uploaded": 0, "originals_failed": 0,
                  "detail": "", "error_code": "", "retry_after_seconds": 300,
                  "unresolved_attachment_count": 0, "archive_sha256s": [], "archives_rejected": 0}
        try:
            self.heartbeat(job, force=True)
            urls = self.http.soap(reg)
            if not urls:
                report.update(status="no_data", detail="ЕИС не вернула архив по номеру закупки.",
                              retry_after_seconds=1200)
                return report
            for url in urls:
                self.heartbeat(job)
                key, data = self.get_file(reg, "archive", url, MAX_ZIP)
                verify_zip(data)
                try:
                    receipt = self.http.upload("/api/v294/eis223/archive", data, "application/zip")
                except WorkerError as exc:
                    if exc.code != "site_http_422":
                        raise
                    self.state.discard_download(key)
                    report["archives_rejected"] += 1
                    report.update(error_code="site_http_422", retry_after_seconds=min(86400, exc.retry_after))
                    log("archive_rejected", reg_number=reg, code=exc.code)
                    continue
                digest = hashlib.sha256(data).hexdigest()
                report["archive_sha256"] = digest
                report["archive_sha256s"].append(digest)
                report["unresolved_attachment_count"] += max(
                    int(receipt.get("unresolved_attachment_count", 0)),
                    int(receipt.get("unsupported_attachment_count", 0)))
                self.state.delivered(reg, "archive", hashlib.sha256(data).hexdigest(), data, key)
                report["archives_uploaded"] += 1
                self.discovery_checkpoint()
            if job.get("metadata_only") is True:
                # The server independently verifies these archives and writes
                # the card before completing a metadata-only lease. Originals
                # remain the responsibility of full-document jobs.
                report.update(status="error" if report["archives_rejected"] else "partial",
                              detail="Часть архивов не принята сайтом; получение будет повторено." if report["archives_rejected"] else
                                     "Официальные XML переданы для обновления карточки.")
                return report
            manifest = self.http.site("/api/v295/eis223/manifest/" + reg)
            documents = manifest.get("documents")
            if not isinstance(documents, list):
                raise WorkerError("manifest_missing_documents")
            with contextlib.closing(OriginalPipeline(self, reg, documents)) as pipeline:
                while not STOP:
                    self.heartbeat(job)
                    transfer = pipeline.take()
                    if transfer is None:
                        break
                    try:
                        key, data = pipeline.result(transfer)
                        # Overlap only the next download with this upload. EIS
                        # requests remain serialized and spaced by 2.5 seconds.
                        pipeline.prefetch()
                        uid = transfer["uid"]
                        self.http.upload("/api/v295/eis223/original/" + reg + "/" + uid,
                                         data, "application/octet-stream")
                        self.state.delivered(reg, "original", uid, data, key)
                        report["originals_uploaded"] += 1
                        self.discovery_checkpoint(pipeline)
                    except WorkerError as exc:
                        if global_backoff(exc.code):
                            self.state.defer_requests(exc.retry_after, site=exc.code.startswith("site_"))
                        report["originals_failed"] += 1
                        if isinstance(exc, ResponseSizeLimit):
                            report["size_limit"] = {**exc.context, "uid": transfer["uid"]}
                        report.update(error_code=exc.code, retry_after_seconds=min(86400, exc.retry_after))
                        if exc.code == "site_http_422":
                            self.state.discard_download(transfer["key"])
                            log("original_rejected", reg_number=reg, code=exc.code)
                            continue
                        if (global_backoff(exc.code) or exc.retry_after >= 1200 or exc.code.startswith("site_")
                                or exc.code == "invalid_manifest_uid"):
                            raise
            # Server receipts, not local successful calls, are authoritative.
            current = self.http.site("/api/v295/eis223/manifest/" + reg)
            docs = current.get("documents")
            if not isinstance(docs, list):
                raise WorkerError("manifest_missing_documents")
            missing = sum(d.get("received") is not True for d in docs)
            report.update(originals_expected=len(docs), originals_received=len(docs) - missing)
            unresolved = max(report["unresolved_attachment_count"],
                             int(current.get("unresolved_attachment_count", 0)),
                             int(current.get("unsupported_attachment_count", 0)))
            report["unresolved_attachment_count"] = unresolved
            if report["archives_rejected"]:
                # Missing archives can contain additional references absent from
                # this manifest. Never let the partial known set imply complete.
                report.update(status="error", detail="Часть архивов не принята сайтом; получение будет повторено.",
                              error_code="site_http_422")
            elif unresolved:
                report.update(status="partial", detail="XML сохранены; часть ссылок не поддерживается приёмником.",
                              error_code="unsupported_attachment_links", retry_after_seconds=1200)
            elif not docs:
                report.update(status="no_data", detail="XML сохранены; ссылки на оригиналы отсутствуют.",
                              retry_after_seconds=1200)
            elif missing:
                report.update(status="partial", detail="XML сохранены; часть оригиналов ожидает загрузки.")
            else:
                review = self.http.site("/api/v295/eis223/recheck/" + reg, {}, method="POST")
                report.update(status="completed", detail="Все оригиналы переданы; проверка выполняется отдельно.",
                              review_status=str(review.get("status", "queued"))[:50], retry_after_seconds=0)
        except WorkerError as exc:
            if isinstance(exc, ResponseSizeLimit):
                report["size_limit"] = exc.context
            if global_backoff(exc.code):
                # Keep the full remote pause locally, even beyond the report's
                # one-day wire limit. A subsequent cycle must not shorten it.
                self.state.defer_requests(exc.retry_after, site=exc.code.startswith("site_"))
            report.update(status="partial" if report["archives_uploaded"] and not report["archives_rejected"] else "error",
                          error_code=exc.code, retry_after_seconds=min(86400, exc.retry_after),
                          detail="Загрузка будет продолжена после повторной попытки.")
        return report

    def cycle(self):
        self.files_this_cycle = 0
        if float(self.state.get("site_backoff_until", "0")) > time.time():
            return
        self.retry_reports()
        self.retry_discovery()
        now = time.time()
        if float(self.state.get("backoff_until", "0")) > now:
            self.heartbeat(force=True)
            return
        self.heartbeat(force=True)
        # Alternate occupied slots under sustained INN demand. Otherwise give
        # queued INN work first refusal, then discover before a long document job.
        if self.state.get("last_search_kind") == "inn":
            self.discover()
        inn_processed = False
        for _ in range(MAX_JOBS_PER_CYCLE):
            if (STOP or self.files_this_cycle >= MAX_ORIGINALS_PER_CYCLE
                    or float(self.state.get("backoff_until", "0")) > time.time()):
                break
            # Claim only when ready to process: a large first procurement must
            # not let the second procurement's unused 15-minute lease expire.
            offer_inn = not inn_processed and self.search_ready()
            result = self.http.site("/api/eis-worker/jobs/next?" + urllib.parse.urlencode(
                {"limit": 1, "worker_id": self.worker_id,
                 "capabilities": "inn_search" if offer_inn else ""}))
            jobs = result.get("jobs", [])
            if (not isinstance(jobs, list) or len(jobs) > 1
                    or any(not isinstance(job, dict) for job in jobs)):
                raise WorkerError("invalid_jobs_response")
            if offer_inn:
                # The server prioritizes due INN jobs; a reg/empty response is
                # an explicit opportunity with no due INN, not a blind guess.
                self.state.set("inn_opportunity_pending", "0")
            if not jobs:
                self.discover()
                break
            job = jobs[0]
            is_inn = job.get("kind") == "inn"
            if is_inn and inn_processed:
                raise WorkerError("invalid_jobs_response")
            inn_processed = inn_processed or is_inn
            if not is_inn:
                self.discover()
                if float(self.state.get("backoff_until", "0")) > time.time():
                    break
            self.search_during_documents = not is_inn
            try:
                report = self.process_inn(job) if is_inn else self.process(job)
            finally:
                self.search_during_documents = False
            self.state.save_report(job["id"], report)
            if is_inn:
                log("inn_search_attempt", inn=report["inn"], page=report["page"],
                    requested_status=report["status"], candidates=len(report["reg_numbers"]),
                    buyer_verified=False, error_code=report["error_code"])
            else:
                log("delivery_attempt", reg_number=report["reg_number"], requested_status=report["status"],
                    archives=report["archives_uploaded"], originals=report["originals_uploaded"],
                    archives_rejected=report["archives_rejected"], originals_failed=report["originals_failed"],
                    error_code=report["error_code"])
            if not is_inn and global_backoff(report["error_code"]):
                self.state.set("backoff_until", max(float(self.state.get("backoff_until", "0")),
                                                    time.time() + report["retry_after_seconds"]))
            # Site Retry-After covers reports too. Keep the report on disk;
            # an expired lease will safely reconcile with server receipts.
            if float(self.state.get("site_backoff_until", "0")) > time.time():
                break
            self.retry_reports()
            self.discover()
            if float(self.state.get("backoff_until", "0")) > time.time():
                break
        if not STOP and float(self.state.get("backoff_until", "0")) <= time.time():
            self.discover()
        self.heartbeat(force=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="/etc/eis223-worker/credentials.json")
    parser.add_argument("--state-dir", default="/var/lib/eis223-worker")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--bootstrap-reg", action="append", default=[])
    args = parser.parse_args(argv)
    os.umask(0o077)
    config = load_config(args.config)
    state = State(args.state_dir)
    # A lock stops concurrent timer/manual processes from claiming duplicate work.
    import fcntl
    with open(state.root / "worker.lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise WorkerError("worker_already_running") from None
        worker = Worker(config, state)
        log("worker_started", version=VERSION, interval_seconds=config["interval_seconds"],
            originals_per_cycle=MAX_ORIGINALS_PER_CYCLE, search_interval_seconds=worker.search_interval)
        for reg in args.bootstrap_reg:
            if not REG.fullmatch(reg):
                raise WorkerError("invalid_registry_number")
            worker.http.site("/api/eis-worker/jobs", {"reg_number": reg, "source": "worker_bootstrap"})
        while not STOP:
            started = time.monotonic()
            try:
                worker.cycle()
            except WorkerError as exc:
                log("cycle_error", code=exc.code)
                if global_backoff(exc.code):
                    state.defer_requests(exc.retry_after, site=exc.code.startswith("site_"))
            if args.once:
                break
            # Bounded waits permit orderly systemd shutdown.
            remaining = config["interval_seconds"] - (time.monotonic() - started)
            while remaining > 0 and not STOP:
                time.sleep(min(1, remaining))
                try:
                    worker.heartbeat()
                except WorkerError as exc:
                    # Avoid a per-second retry loop after an unavailable heartbeat endpoint.
                    worker.last_heartbeat = time.monotonic()
                    log("heartbeat_error", code=exc.code)
                remaining = config["interval_seconds"] - (time.monotonic() - started)


def stop(signum, frame):
    global STOP
    STOP = True


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        main()
    except WorkerError as exc:
        log("fatal", code=exc.code)
        sys.exit(1)
    except Exception as exc:
        # Exception text may contain signed download URLs or credential file content.
        log("fatal", code="unexpected_" + type(exc).__name__)
        sys.exit(1)
