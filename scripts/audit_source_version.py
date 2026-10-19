"""Cheap change detection for repeated audits of saved source documents.

This snapshot schedules another audit; it is not proof of authenticity. The
legal engines still validate the official receipt, file hash and quoted text.
Call before analysis, outside a write transaction, so evidence arriving during
analysis remains eligible for the next pass.
"""

import hashlib
import json
import os
import stat


_DOC_FIELDS = (
    "id", "name", "url", "doc_type", "published_at", "local_path", "status",
    "metadata_json", "final_url", "text_chars",
)
_SOURCE_FIELDS = (
    "source", "source_url", "uid", "sha256", "archive_sha256", "archive_name",
    "archive_member", "filename", "identical_sources",
)


def _rows(conn, query, args=()):
    cursor = conn.execute(query, args)
    try:
        return cursor.fetchall()
    finally:
        cursor.close()


def _metadata(value):
    try:
        parsed = json.loads(value or "{}")
    except (ValueError, TypeError):
        return {"unparsed": str(value or "")}
    if not isinstance(parsed, dict):
        return {"unparsed": parsed}
    # Operational timestamps and extraction warnings must not cause endless
    # repeated audits of the same unchanged original.
    return {key: parsed[key] for key in _SOURCE_FIELDS if key in parsed}


def _file_version(path):
    if not path:
        return ["no_path"]
    try:
        info = os.stat(path)
    except OSError as exc:
        # Missing or temporarily inaccessible originals are input states too.
        # A restored file changes this marker even if docs.id is unchanged.
        return ["unavailable", exc.errno]
    except (TypeError, ValueError):
        return ["invalid_path"]
    return ["file", stat.S_IFMT(info.st_mode), info.st_dev, info.st_ino,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def source_version(conn, reg) -> str:
    """Return a stable SHA256 of DB source fields and file metadata, not bytes.

    Older/minimal schemas are supported. Exhaust and close the SQL reader
    before filesystem calls; large campaigns must not retain a read lock while
    inspecting paths on the persistent volume.
    """
    columns = {row[1] for row in _rows(conn, "PRAGMA table_info(docs)")}
    fields = [name for name in _DOC_FIELDS if name in columns]
    documents = []
    if "reg_number" in columns and fields:
        order = ' ORDER BY "id"' if "id" in columns else ""
        query = "SELECT " + ",".join('"' + name + '"' for name in fields)
        query += ' FROM docs WHERE reg_number=?' + order
        documents = [dict(zip(fields, row)) for row in _rows(conn, query, (reg,))]
    for document in documents:
        if "metadata_json" in document:
            document["metadata_json"] = _metadata(document["metadata_json"])
        document["file_version"] = _file_version(document.get("local_path"))
    # Sorting also keeps schemas without a numeric id deterministic.
    encoded = sorted(json.dumps(document, ensure_ascii=False, sort_keys=True,
                                separators=(",", ":"), default=str)
                     for document in documents)
    payload = json.dumps({"format": 1, "documents": encoded},
                         ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
