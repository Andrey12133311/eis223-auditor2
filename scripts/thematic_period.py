"""Inclusive EIS publication-date selection shared by thematic entry points."""
from __future__ import annotations

from datetime import date, datetime
import re


def normalize_period(date_from=None, date_to=None):
    """Accept calendar dates only; empty bounds keep the historical all-time scope."""
    values = []
    for value, label in ((date_from, "начала"), (date_to, "окончания")):
        if value is None or value == "":
            values.append("")
            continue
        if isinstance(value, date) and not isinstance(value, datetime):
            value = value.isoformat()
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError(f"Укажите дату {label} периода в формате ГГГГ-ММ-ДД")
        try:
            values.append(date.fromisoformat(value).isoformat())
        except ValueError as exc:
            raise ValueError(f"Указана несуществующая дата {label} периода") from exc
    if values[0] and values[1] and values[0] > values[1]:
        raise ValueError("Дата начала периода не может быть позже даты окончания")
    return tuple(values)


def publication_day(value):
    """Use the calendar day stated by EIS, without a UTC or browser-zone shift."""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str):
        return None
    value = value.strip()
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})(?=$|[T\s])", value)
    if match:
        parts = [int(part) for part in match.groups()]
    else:
        match = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})(?=$|\s)", value)
        if not match:
            return None
        parts = [int(match[3]), int(match[2]), int(match[1])]
    try:
        return date(*parts).isoformat()
    except ValueError:
        return None


def contains(publication, date_from="", date_to=""):
    if not date_from and not date_to:
        return True
    day = publication_day(publication)
    return bool(day and (not date_from or day >= date_from)
                and (not date_to or day <= date_to))


def period_fields(batch):
    """sqlite.Row and dict supported, including fixtures predating the migration."""
    keys = batch.keys()
    return {key: (batch[key] or "") if key in keys else ""
            for key in ("date_from", "date_to")}


def selection_sql(connection, batch, alias="p"):
    """Parameterized bounds; missing publication facts never match a dated scope."""
    bounds = period_fields(batch)
    clauses, values = [], []
    if bounds["date_from"] or bounds["date_to"]:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(purchases)")}
        expression = f"eis_publication_day({alias}.published_at)" if "published_at" in columns else "NULL"
        connection.create_function("eis_publication_day", 1, publication_day, deterministic=True)
        for field, operator in (("date_from", ">="), ("date_to", "<=")):
            if bounds[field]:
                clauses.append(f"{expression} {operator} ?")
                values.append(bounds[field])
    return " AND ".join(clauses) or "1=1", values


def joined_selection_sql(connection):
    """Same selection for queries joining multiple batches b to purchases p."""
    columns = {row[1] for row in connection.execute("PRAGMA table_info(purchases)")}
    expression = "eis_publication_day(p.published_at)" if "published_at" in columns else "NULL"
    connection.create_function("eis_publication_day", 1, publication_day, deterministic=True)
    return (f"(b.date_from='' OR {expression}>=b.date_from) "
            f"AND (b.date_to='' OR {expression}<=b.date_to)")


def index_campaign(connection, batch, customer_form, timestamp):
    """Add actual matching purchases, and keep corrected dates out of execution."""
    import json

    allowed = set(json.loads(batch["legal_forms"]))
    scope, values = selection_sql(connection, batch)
    last_reg = ""
    while True:
        # Finish and close each read statement before its first write. A
        # streaming SELECT kept a WAL snapshot open across INSERT; another
        # EIS writer could then make the upgrade fail with BUSY_SNAPSHOT.
        # Keyset pages bound memory without capping the complete selection.
        cursor = connection.execute(
            "SELECT p.reg_number,p.customer FROM purchases p "
            "WHERE p.reg_number GLOB '3??????????' AND p.reg_number>? AND "
            + scope + " ORDER BY p.reg_number LIMIT 1000", [last_reg, *values])
        try:
            rows = cursor.fetchall()
        finally:
            cursor.close()
        if not rows:
            break
        # Advance past every fetched purchase, including excluded forms.
        last_reg = rows[-1]["reg_number"]
        queue = [(batch["id"], row["reg_number"], timestamp) for row in rows
                 if customer_form(row["customer"]) in allowed]
        _insert_items(connection, queue)
    bounds = period_fields(batch)
    if bounds["date_from"] or bounds["date_to"]:
        # Preserve saved evidence if EIS later corrects a publication date;
        # exclude the item from further work rather than deleting its history.
        connection.execute(
            "UPDATE thematic_items SET status='outside_period',"
            "detail='Дата размещения закупки не входит в выбранный период',updated_at=? "
            "WHERE batch_id=? AND status<>'outside_period' AND NOT EXISTS ("
            "SELECT 1 FROM purchases p WHERE p.reg_number=thematic_items.reg AND "
            + scope + ")", [timestamp, batch["id"], *values])


def _insert_items(connection, queue):
    # sqlite3.executemany([]) can start a deferred transaction even without
    # rows. That would pin the next read snapshot before a later page writes.
    if not queue:
        return
    connection.executemany(
        "INSERT INTO thematic_items(batch_id,reg,updated_at) VALUES(?,?,?) "
        "ON CONFLICT(batch_id,reg) DO UPDATE SET status='queued',detail='',"
        "updated_at=excluded.updated_at WHERE thematic_items.status='outside_period'", queue)
