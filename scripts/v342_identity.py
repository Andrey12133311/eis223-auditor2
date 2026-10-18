"""Unicode-safe validation of buyer identities and procurement subjects."""
from __future__ import annotations
import re


def compact(value):
    return " ".join(str(value or "").replace("\x00", " ").split())


def organization_display_name(value):
    """Capitalize only the initial inside a quoted organization name.

    Preserve the source spelling, abbreviations and mixed case everywhere
    else. Track paired quotes so a closing straight quote cannot capitalize
    the next word (for example, '"Центр" филиал'). No storage is modified.
    """
    chars = list(str(value or ""))
    closing = []
    pairs = {"«": "»", '"': '"', "„": "“", "“": "”"}
    for index, char in enumerate(chars):
        if closing and char == closing[-1]:
            closing.pop()
            continue
        if char not in pairs:
            continue
        closing.append(pairs[char])
        initial = index + 1
        while initial < len(chars) and chars[initial].isspace():
            initial += 1
        if initial < len(chars) and chars[initial].isalpha():
            chars[initial] = chars[initial].upper()
    return "".join(chars)


def customer_label(value):
    name = compact(value).strip(" |:;,")
    name = re.sub(
        r"^(?:наименование\s*(?:организации|заказчика)?\s*"
        r"(?:\(\s*полное(?:\s*[,;и]\s*(?:сокращенное|сокращённое))?\s*\))?"
        r"|полное\s+наименование(?:\s+организации|\s+заказчика)?)\s*[:：–—-]\s*",
        "", name, flags=re.I)
    if re.fullmatch(r"(?:наименование|полное наименование|сведения о заказчике|"
                    r"информация о заказчике|заказчик)(?:\s*\([^)]*\))?", name, re.I):
        return ""
    if re.match(r"^(?:уточня\w*|заказчик уточняется|не указан|не найден|"
                r"сведения отсутствуют|нет данных|unknown)(?:\s|$)", name, re.I):
        return ""
    # Merged table cells can repeat the same legal name several times.
    for count in (3, 2):
        size = (len(name) - count + 1) // count
        part = name[:size].strip()
        if len(part) >= 10 and name == " ".join([part]*count):
            return part
    return name


def valid_inn(value):
    value = compact(value)
    match = re.fullmatch(r"(?:ИНН\s*[:：]?\s*)?(\d{10}|\d{12})"
                         r"(?:\s*/\s*(?:КПП\s*[:：]?\s*)?\d{9})?", value, re.I)
    if not match:
        return ""
    value = match[1]
    if len(set(value)) == 1:
        return ""
    digits = list(map(int, value))
    def check(weights):
        return sum(a*b for a, b in zip(weights, digits)) % 11 % 10
    if len(value) == 10:
        ok = check((2, 4, 10, 3, 5, 9, 4, 6, 8)) == digits[9]
    else:
        ok = (check((7, 2, 4, 10, 3, 5, 9, 4, 6, 8)) == digits[10] and
              check((3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)) == digits[11])
    return value if ok else ""


def subject(value):
    value = compact(value).strip(" |:;,–—-")[:800]
    for _ in range(3):
        value = re.sub(r"^(?:предмет|объект|наименование) закупки\s*[:：–—-]\s*", "", value, flags=re.I)
    if len(value) < 5 or not re.search(r"[а-яёa-z]{3}", value, re.I):
        return ""
    if re.fullmatch(
        r"(?:подача заявок|при[её]м заявок(?: окончен)?|(?:начало|окончание) подачи заявок|"
        r"подведение итогов|работа комиссии|рассмотрение(?: и оценка)? заявок|"
        r"оценка заявок|определение поставщика|заключение договора|"
        r"закупка (?:завершена|отменена)|опубликовано|размещено|"
        r"ид[её]т при[её]м заявок|извещение|статус закупки)", value.rstrip(" ."), re.I):
        return ""
    if re.match(r"^(?:статус(?: закупки)?|этап закупки|стадия закупки)\s*[:：–—-]", value, re.I):
        return ""
    if re.match(r"^закупка\s*№?\s*3\d{10}(?:\s|$)", value, re.I):
        return ""
    if re.match(r"^закупка\s*[-–—]\s*(?:совокупность|действия|процесс)", value, re.I):
        return ""
    if re.match(r"^поставка\s+(?:товара|продукции|оборудования)\s+(?:по договору\s+)?"
                r"(?:осуществляется|производится)", value, re.I):
        return ""
    if re.search(r"\bв месяце\s*_{2,}", value, re.I):
        return ""
    return value
