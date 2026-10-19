"""Cheap change detection for repeated audits of saved source documents.

This snapshot schedules another audit; it is not proof of authenticity. The
legal engines still validate the official receipt, file hash and quoted text.
Call before analysis, outside a write transaction, so evidence arriving during
analysis remains eligible for the next pass.
"""

import hashlib
import json
import os
import re
import stat


_DOC_FIELDS = (
    "id", "name", "url", "doc_type", "published_at", "local_path", "status",
    "metadata_json", "final_url", "text_chars",
)
_SOURCE_FIELDS = (
    "source", "source_url", "uid", "sha256", "archive_sha256", "archive_name",
    "archive_member", "filename", "identical_sources",
)
_OPERATIONAL_FIELDS = frozenset((
    "checked_at", "received_at", "captured_at", "updated_at", "extracted_at",
    "last_checked_at", "downloaded_at", "error", "state", "extraction_ms",
    "duration_ms", "annotated",
))


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
    result = {key: parsed[key] for key in _SOURCE_FIELDS if key in parsed}
    if isinstance(result.get("identical_sources"), list):
        def stable(value):
            if isinstance(value, dict):
                return {key: stable(item) for key, item in value.items()
                        if key not in _OPERATIONAL_FIELDS}
            if isinstance(value, list):
                return [stable(item) for item in value]
            return value
        aliases = {json.dumps(stable(alias), ensure_ascii=False, sort_keys=True,
                              separators=(",", ":"), default=str)
                   for alias in result["identical_sources"]}
        result["identical_sources"] = [json.loads(alias) for alias in sorted(aliases)]
    return result


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


def original_sources(reg):
    """Describe allowed, locally saved originals without reading their bytes.

    This is scheduling metadata only. A receipt's size/path is enough to wake
    a reader; that reader must still authenticate the actual SHA256 before
    using any text. Operational receipt fields do not change this snapshot.
    """
    if not re.fullmatch(r"[0-9]{11,20}", str(reg)):
        return []
    from v295_eis_originals import (allowed_documents, saved_document,
                                    _review_source_version, HTTPException)
    try:
        allowed = allowed_documents(reg)
        # Intake already defines stable manifest/SOAP facts. This hash only
        # schedules an audit; the legal reader still verifies original bytes.
        review_version = _review_source_version(reg)
    except HTTPException as exc:
        if exc.status_code != 409:
            raise
        # One bounded/blocked catalogue must not stop another procurement's
        # scheduling or initialize(). It is not a received original.
        return [{"available": False, "scan_error": str(exc.detail)}]
    sources = []
    for uid, item in sorted(allowed.items()):
        source = {"uid": uid, "name": item.get("name"),
                  "url": item.get("url"), "ext": item.get("ext")}
        saved = saved_document(reg, uid)
        source["available"] = saved is not None
        if saved is not None:
            path, receipt = saved
            source.update(sha256=receipt.get("sha256"),
                          extension=receipt.get("extension"),
                          bytes=receipt.get("bytes"),
                          file_version=_file_version(path))
        sources.append(source)
    sources.append({"available": False, "legal_source_version": review_version})
    return sources


def originals_ready(reg):
    """Whether the local receipt intake can be read before docs are saved."""
    return any(source["available"] for source in original_sources(reg))


def originals_blocked(reg):
    """An explicit catalogue error needs a durable partial state, not intake."""
    return any(source.get("scan_error") for source in original_sources(reg))


def source_version(conn, reg, *, originals=None) -> str:
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
    if originals is None:
        originals = original_sources(reg)
    payload = json.dumps({"format": 2, "documents": encoded,
                          "originals": originals},
                         ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
