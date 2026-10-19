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
from collections import OrderedDict
from threading import Lock


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


class SemanticSnapshotError(RuntimeError):
    """The bounded source snapshot is incomplete; do not acknowledge a version."""

    def __init__(self, reason, detail=""):
        self.reason = reason
        self.detail = str(detail)
        super().__init__(reason + (": " + self.detail if detail else ""))

    @property
    def diagnostic_code(self):
        # Emit only the bounded machine reason, never source paths/metadata.
        reason = self.reason if isinstance(self.reason, str) and re.fullmatch(r"[a-z_]{1,48}", self.reason) else "unknown"
        return "source_snapshot_" + reason


SEMANTIC_MAX_ROWS = 10000
SEMANTIC_MAX_FILES = 12000
SEMANTIC_MAX_BYTES = 256 * 1024 * 1024
SEMANTIC_MAX_SECONDS = 20
SEMANTIC_JSON_BYTES = 8 * 1024 * 1024
SEMANTIC_CACHE_ENTRIES = 12000
SEMANTIC_XML_CACHE_BYTES = 32 * 1024 * 1024
SEMANTIC_JSON_CACHE_BYTES = 64 * 1024 * 1024
SEMANTIC_PROGRESS_LEASE_SECONDS = 6 * 60 * 60
_HASH_CACHE = OrderedDict()
_XML_CACHE = OrderedDict()
_JSON_CACHE = OrderedDict()
_CACHE_LOCK = Lock()
_PROTECTED_SOURCE = None
_PROTECTED_LAST_ACTIVE = 0.0


def _physical_identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def clear_semantic_cache():
    """Discard process-local scheduling acceleration, never source evidence."""
    global _PROTECTED_SOURCE, _PROTECTED_LAST_ACTIVE
    with _CACHE_LOCK:
        _HASH_CACHE.clear()
        _XML_CACHE.clear()
        _JSON_CACHE.clear()
        _PROTECTED_SOURCE = None
        _PROTECTED_LAST_ACTIVE = 0.0


def _cache_get(cache, key):
    with _CACHE_LOCK:
        result = cache.get(key)
        if result is not None:
            cache.move_to_end(key)
        return result


def _cache_source(key):
    from pathlib import Path
    path = Path(key[0])
    parts = path.parts
    for index in range(len(parts) - 2, -1, -1):
        if parts[index] in ("eis223_official_originals", "eis223_official_archive_inbox",
                            "eis223_public_browser_intake"):
            return str(Path(*parts[:index])), parts[index + 1]
    return None


def _cache_set(cache, key, value):
    with _CACHE_LOCK:
        # A rewritten path no longer needs protected entries for its obsolete
        # physical versions. Verify under the same lock so a late old reader
        # cannot evict a newer writer's current observation. The scan is bounded
        # by the existing entry cap; distinct paths/receipt views stay separate.
        try:
            current = os.stat(key[0], follow_symlinks=False)
        except OSError as exc:
            raise SemanticSnapshotError("file_unreadable", exc.errno) from exc
        if not stat.S_ISREG(current.st_mode) or _physical_identity(current) != key[1]:
            raise SemanticSnapshotError("concurrent_file_change")
        for candidate in list(cache):
            if candidate[0] == key[0] and candidate[1] != key[1]:
                del cache[candidate]
        cache[key] = value
        cache.move_to_end(key)
        limit = (SEMANTIC_XML_CACHE_BYTES if cache is _XML_CACHE else
                 SEMANTIC_JSON_CACHE_BYTES if cache is _JSON_CACHE else None)
        def over_limit():
            return len(cache) > SEMANTIC_CACHE_ENTRIES or (
                limit is not None and sum(item[1] for item in cache.values()) > limit)
        while cache and over_limit():
            victim = next((candidate for candidate in cache if
                           _PROTECTED_SOURCE is None or _cache_source(candidate) != _PROTECTED_SOURCE), None)
            if victim is None:
                # An individual continuation must fit the same fixed memory
                # caps. Never silently evict its prefix and retry forever.
                cache.pop(key, None)
                raise SemanticSnapshotError("resume_capacity")
            del cache[victim]


def _progress_time():
    import time
    return time.monotonic()


def _begin_semantic_progress(source):
    global _PROTECTED_SOURCE, _PROTECTED_LAST_ACTIVE
    with _CACHE_LOCK:
        now = _progress_time()
        if _PROTECTED_SOURCE is not None and now - _PROTECTED_LAST_ACTIVE >= SEMANTIC_PROGRESS_LEASE_SECONDS:
            # Eligibility/campaign removal may prevent another call for this
            # source. A stale lease must not pin capacity for the process life.
            _PROTECTED_SOURCE = None
            _PROTECTED_LAST_ACTIVE = 0.0
        if _PROTECTED_SOURCE == source:
            _PROTECTED_LAST_ACTIVE = now


def _settle_semantic_progress(source, error=None):
    global _PROTECTED_SOURCE, _PROTECTED_LAST_ACTIVE
    with _CACHE_LOCK:
        if error is not None and error.reason in ("byte_budget", "time_budget", "file_budget"):
            if _PROTECTED_SOURCE is None:
                _PROTECTED_SOURCE = source
            if _PROTECTED_SOURCE == source:
                _PROTECTED_LAST_ACTIVE = _progress_time()
        elif _PROTECTED_SOURCE == source:
            _PROTECTED_SOURCE = None
            _PROTECTED_LAST_ACTIVE = 0.0

_CARD_FIELDS = ("title", "customer", "customer_inn", "method", "purchase_method",
                "published", "published_at", "submission_start", "submission_end",
                "deadline", "price", "price_num", "price_kind", "sme_only")
_PRICE_FIELDS = ("price", "price_num", "price_kind", "price_label", "currency")
_DOCUMENT_IDENTITY = ("name", "url", "doc_type", "published_at")
_MANIFEST_EVENT_FIELDS = ("type", "number", "event_number", "version", "guid",
    "source_page", "source_url", "document_name", "publication_date", "published_at",
    "decision_date", "submission_start", "submission_end", "request_date", "answer_date",
    "signed_at", "decision_evidence", "publication_evidence", "evidence")


def _semantic_json(value, label):
    if not value:
        return {}
    if not isinstance(value, str) or len(value.encode("utf-8")) > SEMANTIC_JSON_BYTES:
        raise SemanticSnapshotError("metadata_size", label)
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError, RecursionError) as exc:
        raise SemanticSnapshotError("invalid_metadata", label) from exc
    if not isinstance(parsed, dict):
        raise SemanticSnapshotError("invalid_metadata", label)
    return parsed


def _selected(value, names):
    return {key: value[key] for key in names if key in value and value[key] not in (None, "")}


def _canonical(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                          allow_nan=False)
    except (ValueError, TypeError, RecursionError) as exc:
        raise SemanticSnapshotError("invalid_semantic_value") from exc


def _unique(items):
    return [json.loads(item) for item in sorted({_canonical(item) for item in items})]


def _semantic_rows(con, table, key, reg, wanted):
    columns = {row[1] for row in _rows(con, 'PRAGMA table_info("' + table + '")')}
    fields = [field for field in wanted if field in columns]
    if key not in columns or not fields:
        return []
    query = "SELECT " + ",".join(
        f"CASE WHEN typeof(\"{name}\") IN ('text','blob') THEN substr(\"{name}\",1," +
        str(SEMANTIC_JSON_BYTES + 1) + ') ELSE "' + name + '" END' for name in fields)
    query += ' FROM "' + table + '" WHERE "' + key + '"=? LIMIT ?'
    cursor = con.execute(query, (reg, SEMANTIC_MAX_ROWS + 1))
    found, size = [], 0
    try:
        for row in cursor:
            for value in row:
                length = len(value.encode("utf-8")) if isinstance(value, str) else len(value) if isinstance(value, bytes) else 0
                size += length
                if length > SEMANTIC_JSON_BYTES or size > 32 * 1024 * 1024:
                    raise SemanticSnapshotError("database_metadata_size", table)
            found.append(dict(zip(fields, row)))
            if len(found) > SEMANTIC_MAX_ROWS:
                raise SemanticSnapshotError("row_limit", table)
    finally:
        cursor.close()
    return found


class _SemanticIO:
    def __init__(self):
        import time
        self.deadline = time.monotonic() + SEMANTIC_MAX_SECONDS
        self.bytes = 0
        self.files = 0
        self.cache = {}

    def charge(self, size=0):
        import time
        self.bytes += size
        if self.bytes > SEMANTIC_MAX_BYTES:
            raise SemanticSnapshotError("byte_budget")
        if time.monotonic() > self.deadline:
            raise SemanticSnapshotError("time_budget")

    def paths(self, folder, pattern, limit):
        from itertools import islice
        try:
            # scandir surfaces permission failures; Path.glob can suppress them.
            with os.scandir(folder) as entries:
                import fnmatch
                paths = []
                for count, entry in enumerate(islice(entries, limit * 3 + 21)):
                    self.charge()
                    if count >= limit * 3 + 20:
                        raise SemanticSnapshotError("directory_entry_limit", str(folder))
                    if fnmatch.fnmatchcase(entry.name, pattern):
                        paths.append(entry.path)
                        if len(paths) > limit:
                            raise SemanticSnapshotError("entry_limit", str(folder))
        except FileNotFoundError:
            return []
        except OSError as exc:
            raise SemanticSnapshotError("directory_unreadable", exc.errno) from exc
        self.charge()
        if len(paths) > limit:
            raise SemanticSnapshotError("entry_limit", str(folder))
        return sorted(paths)

    def read(self, path, limit, *, optional=False, retain=True, expected=None):
        from pathlib import Path
        path = Path(path)
        key = (str(path), retain)
        if key in self.cache:
            return self.cache[key]
        self.charge()
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
        except FileNotFoundError:
            if optional:
                return None
            raise SemanticSnapshotError("file_missing", path.name)
        except OSError as exc:
            raise SemanticSnapshotError("file_unreadable", exc.errno) from exc
        try:
            with os.fdopen(fd, "rb") as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode):
                    raise SemanticSnapshotError("not_regular_file", path.name)
                if before.st_size > limit:
                    raise SemanticSnapshotError("file_size", path.name)
                shared_key = (str(path.absolute()), _physical_identity(before), expected)
                if expected:
                    cached = _cache_get(_HASH_CACHE, shared_key)
                    # A corrupt digest is enough only for an explicit invalid
                    # source marker. Matching archives still need their bytes
                    # for receipt/member authentication unless XML is cached.
                    if cached is not None and (not retain or cached[0] != expected):
                        if _physical_identity(before) != _physical_identity(path.stat(follow_symlinks=False)):
                            raise SemanticSnapshotError("concurrent_file_change", path.name)
                        return cached
                self.files += 1
                if self.files > SEMANTIC_MAX_FILES:
                    raise SemanticSnapshotError("file_budget")
                digest = hashlib.sha256()
                chunks = []
                size = 0
                while True:
                    chunk = stream.read(min(1024 * 1024, limit + 1 - size))
                    if not chunk:
                        break
                    size += len(chunk)
                    self.charge(len(chunk))
                    if size > limit:
                        raise SemanticSnapshotError("file_size", path.name)
                    digest.update(chunk)
                    if retain:
                        chunks.append(chunk)
                after = os.fstat(stream.fileno())
                current = path.stat(follow_symlinks=False)
                if _physical_identity(before) != _physical_identity(after) or _physical_identity(after) != _physical_identity(current):
                    raise SemanticSnapshotError("concurrent_file_change", path.name)
        except OSError as exc:
            raise SemanticSnapshotError("file_unreadable", exc.errno) from exc
        result = (digest.hexdigest(), size, b"".join(chunks) if retain else None)
        self.cache[key] = result
        if expected and (not retain or result[0] != expected):
            # Only fixed-size verified observations survive bounded passes.
            # Never retain raw ZIP contents or promote corrupt bytes to facts.
            _cache_set(_HASH_CACHE, shared_key, (result[0], result[1], None))
        return result

    def json(self, path, *, optional=False):
        from pathlib import Path
        path = Path(path)
        self.charge()
        try:
            info = path.stat(follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode):
                raise SemanticSnapshotError("not_regular_file", path.name)
            key = (str(path.absolute()), _physical_identity(info))
            cached = _cache_get(_JSON_CACHE, key)
            if cached is not None:
                return cached[0]
        except FileNotFoundError:
            if optional:
                return None
            raise SemanticSnapshotError("file_missing", path.name)
        except OSError as exc:
            raise SemanticSnapshotError("file_unreadable", exc.errno) from exc
        result = self.read(path, SEMANTIC_JSON_BYTES, optional=optional)
        if result is None:
            return None
        try:
            value = json.loads(result[2])
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise SemanticSnapshotError("invalid_metadata", str(path)) from exc
        if not isinstance(value, dict):
            raise SemanticSnapshotError("invalid_metadata", str(path))
        try:
            if _physical_identity(info) != _physical_identity(path.stat(follow_symlinks=False)):
                raise SemanticSnapshotError("concurrent_file_change", path.name)
        except OSError as exc:
            raise SemanticSnapshotError("file_unreadable", exc.errno) from exc
        _cache_set(_JSON_CACHE, key, (value, result[1]))
        return value


def _semantic_xml(io, root, reg):
    import io as streams
    import re
    import zipfile
    from pathlib import Path
    from eis223_soap_xml import parse_document
    folder = root / "eis223_official_archive_inbox" / reg
    records, invalid = [], []
    for filename in io.paths(folder, "*.json", 1000):
        receipt = io.json(filename)
        if receipt.get("source") != "official_eis_soap_archive" or receipt.get("reg_number") != reg:
            continue
        expected = str(receipt.get("archive_sha256") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise SemanticSnapshotError("invalid_archive_receipt", Path(filename).name)
        notices = receipt.get("notices", [])
        if not isinstance(notices, list) or len(notices) > 100:
            raise SemanticSnapshotError("archive_record_limit")
        archive_path = folder / (expected + ".zip")
        try:
            info = archive_path.stat(follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode):
                raise SemanticSnapshotError("not_regular_file", archive_path.name)
            cache_key = (str(archive_path.absolute()), _physical_identity(info), expected,
                         _canonical([_selected(item, ("reg_number", "xml_filename", "xml_sha256"))
                                     for item in notices if isinstance(item, dict)]))
            cached = _cache_get(_XML_CACHE, cache_key)
            if cached is not None:
                records.extend(cached[0])
                continue
        except FileNotFoundError:
            cache_key = None
        except OSError as exc:
            raise SemanticSnapshotError("file_unreadable", exc.errno) from exc
        result = io.read(archive_path, 15 * 1024 * 1024, optional=True, expected=expected)
        if result is None or result[0] != expected:
            invalid.append({"archive": expected, "state": "missing" if result is None else "corrupt",
                            "actual": result[0] if result else None})
            continue
        archive_records = []
        try:
            with zipfile.ZipFile(streams.BytesIO(result[2])) as archive:
                members = [entry for entry in archive.infolist() if not entry.is_dir()]
                if (len(members) > 100 or len({entry.filename for entry in members}) != len(members)
                        or sum(entry.file_size for entry in members) > 50 * 1024 * 1024):
                    raise SemanticSnapshotError("archive_expansion_limit")
                names = {entry.filename: entry for entry in members}
                notices = receipt.get("notices", [])
                if not isinstance(notices, list) or len(notices) > 100:
                    raise SemanticSnapshotError("archive_record_limit")
                verified_members = {}
                for entry in notices:
                    if not isinstance(entry, dict) or entry.get("reg_number") != reg:
                        continue
                    member = names.get(entry.get("xml_filename"))
                    if member is None or member.flag_bits & 1 or member.file_size > 15 * 1024 * 1024:
                        raise SemanticSnapshotError("xml_member_unavailable")
                    # Repeated receipt references may name the same physical
                    # member. Reuse only within this authenticated ZIP, and
                    # still compare every reference's expected XML digest.
                    identity = (member.header_offset, member.filename, member.CRC, member.file_size)
                    if identity in verified_members:
                        if verified_members[identity] != entry.get("xml_sha256"):
                            raise SemanticSnapshotError("xml_receipt_hash_mismatch")
                        continue
                    io.charge(member.file_size)
                    with archive.open(member) as stream:
                        raw = stream.read(15 * 1024 * 1024 + 1)
                    if len(raw) > 15 * 1024 * 1024:
                        raise SemanticSnapshotError("xml_member_size")
                    actual = hashlib.sha256(raw).hexdigest()
                    if actual != entry.get("xml_sha256"):
                        raise SemanticSnapshotError("xml_receipt_hash_mismatch")
                    record, _ = parse_document(raw, member.filename)
                    verified_members[identity] = actual
                    if record["reg_number"] == reg:
                        # XML identity survives repacking/replaying the same SOAP reply.
                        archive_records.append(record)
            if cache_key is not None and _physical_identity(info) == _physical_identity(archive_path.stat(follow_symlinks=False)):
                _cache_set(_XML_CACHE, cache_key, (archive_records, len(_canonical(archive_records).encode("utf-8"))))
            records.extend(archive_records)
        except (ValueError, KeyError, zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
            if isinstance(exc, SemanticSnapshotError):
                raise
            raise SemanticSnapshotError("xml_unreadable", type(exc).__name__) from exc
    return _unique(records), _unique(invalid)


def _semantic_manifest(io, root, reg):
    from v295_eis_originals import uid_from_url
    value = io.json(root / "eis223_public_browser_intake" / reg / "manifest.json", optional=True)
    if not value or value.get("source") != "public_eis_223_browser" or value.get("reg_number") != reg:
        return {}
    result = _selected(value, _CARD_FIELDS + ("source", "source_page", "reg_number"))
    documents = value.get("documents") or []
    if not isinstance(documents, list) or len(documents) > SEMANTIC_MAX_ROWS:
        raise SemanticSnapshotError("manifest_document_limit")
    result["documents"] = _unique([_selected(item, ("name", "url", "type", "published", "published_at", "guid", "version"))
        for item in documents if isinstance(item, dict) and uid_from_url(item.get("url") or "")])
    for kind in ("changes", "clarifications", "protocols", "cancellations"):
        events = value.get(kind) or []
        if not isinstance(events, list) or len(events) > SEMANTIC_MAX_ROWS:
            raise SemanticSnapshotError("manifest_event_limit", kind)
        selected = [_selected(item, _MANIFEST_EVENT_FIELDS) for item in events if isinstance(item, dict)]
        if any(selected):
            result[kind] = _unique([item for item in selected if item])
    return result


def _semantic_originals(io, root, reg, documents, manifest, xml):
    import re
    from pathlib import Path
    from v295_eis_originals import EXT, uid_from_url
    references = {}
    for entry in manifest.get("documents", []) + [doc for record in xml for doc in record.get("documents", [])]:
        uid = uid_from_url(entry.get("url") or "")
        if uid:
            references.setdefault(uid, []).append(entry)
    candidates = {}
    for document in documents:
        try:
            meta = _semantic_json(document.get("metadata_json"), "docs.metadata_json")
        except SemanticSnapshotError:
            # A malformed docs-only row cannot supply official source evidence.
            continue
        uid = uid_from_url(meta.get("source_url") or document.get("url") or "")
        if uid and meta.get("source") in ("official_eis_original", "official_eis_archive_member"):
            candidates.setdefault(uid, []).append((document, meta))
    folder = root / "eis223_official_originals" / reg
    uids = set(references) | set(candidates)
    uids.update(Path(path).name for path in io.paths(folder, "*", SEMANTIC_MAX_ROWS)
                if re.fullmatch(r"[0-9A-F]{32}", Path(path).name))
    sources = []
    for uid in sorted(uids):
        receipt = io.json(folder / uid / "receipt.json", optional=True)
        if receipt is None:
            continue
        expected, extension = receipt.get("sha256"), receipt.get("extension")
        if (receipt.get("reg_number") != reg or receipt.get("uid") != uid
                or extension not in EXT or not re.fullmatch(r"[0-9a-f]{64}", str(expected))
                or not isinstance(receipt.get("bytes"), int) or receipt["bytes"] < 0):
            raise SemanticSnapshotError("invalid_original_receipt", uid)
        path = folder / uid / (expected + extension)
        result = io.read(path, 25 * 1024 * 1024, optional=True, retain=False, expected=expected)
        source = {"uid": uid, "expected": expected, "extension": extension,
                  "state": "missing" if result is None else "verified" if
                  result[0] == expected and result[1] == receipt["bytes"] else "corrupt"}
        if result:
            source.update(actual=result[0], bytes=result[1])
        receipt_url = receipt.get("source_url")
        if uid_from_url(receipt_url or "") == uid:
            source.update(_selected(receipt, ("source_url", "name")))
        identities = []
        for document, meta in candidates.get(uid, []):
            if source["state"] != "verified":
                continue
            # A row's provenance must match the current authenticated original.
            if meta.get("source") == "official_eis_original":
                if meta.get("sha256") != expected or not document.get("local_path"):
                    continue
                try:
                    if Path(document["local_path"]).resolve() != path.resolve():
                        continue
                except OSError as exc:
                    raise SemanticSnapshotError("original_path_unavailable", uid) from exc
                identities.append(_selected(document, _DOCUMENT_IDENTITY))
            # Archive-member rows are regenerated from the authenticated parent.
            # Their local paths, text, role annotations and claimed membership
            # cannot become independent source proof or retrigger extraction.
        if identities:
            source["document_identity"] = _unique(identities)
        sources.append(source)
    return sources


def _semantic_cards(purchases, facts, prices, reg):
    from v311_verified_customer import _official_source
    result = {}
    for row in facts:
        payload = _semantic_json(row.get("payload"), "v341_card_facts.payload")
        source = payload.get("source_page") or row.get("source_page")
        if not _official_source(source, reg):
            continue
        fields = payload.get("fields") or {}
        if not isinstance(fields, dict):
            raise SemanticSnapshotError("invalid_card_fields")
        result["direct"] = {"source_page": source, "fields": _selected(fields, _CARD_FIELDS),
                            "price_formula": payload.get("price_formula") or ""}
        values = payload.get("prices") or []
        if not isinstance(values, list):
            raise SemanticSnapshotError("invalid_card_prices")
        result["direct"]["prices"] = _unique([_selected(price, _PRICE_FIELDS) for price in values if isinstance(price, dict)])
    for row in prices:
        if _official_source(row.get("source_page"), reg):
            result["price"] = _selected(row, _PRICE_FIELDS + ("source_page", "conflict", "alternative_price"))
    for row in purchases:
        card = _semantic_json(row.get("card_fields_json"), "purchases.card_fields_json")
        # Only source-labelled card facts are independent evidence. Ignore
        # aggregate scope, extracted document facts, coverage and cache output.
        fallback = {}
        card_evidence = card.get("fact_evidence") or {}
        if not isinstance(card_evidence, dict):
            raise SemanticSnapshotError("invalid_card_evidence")
        for key, evidence in card_evidence.items():
            if not isinstance(evidence, dict) or key not in _CARD_FIELDS:
                continue
            if key in result.get("direct", {}).get("fields", {}):
                continue
            if evidence.get("source") == "official_eis_card" and _official_source(evidence.get("source_url"), reg):
                fallback[key] = _selected(evidence, ("value", "source", "source_url"))
        price = card.get("price_evidence") or {}
        if (not result.get("direct", {}).get("prices") and "price" not in result
                and isinstance(price, dict) and price.get("source") == "official_eis_card"
                and _official_source(price.get("source_url") or price.get("evidence_page"), reg)):
            fallback["price_evidence"] = _selected(price, _PRICE_FIELDS + ("source", "source_url", "evidence_page"))
        if fallback:
            result["saved_card"] = fallback
        if row.get("source") in ("official_eis_getDocsIP", "public_eis_223_browser", "official_eis_card") or _official_source(row.get("url"), reg):
            current = _selected(row, _CARD_FIELDS)
            for key in result.get("direct", {}).get("fields", {}):
                current.pop("published_at" if key == "published" else key, None)
                if key == "submission_end":
                    current.pop("deadline", None)
            if result.get("direct", {}).get("prices") or ("price" in result and not result["price"].get("conflict")):
                for key in ("price", "price_num", "price_kind"):
                    current.pop(key, None)
            if current:
                result["current"] = current
    return result


def _semantic_source_version(con, reg) -> str:
    """Read bounded source bytes, outside SQL transactions, for recheck scheduling.

    This token never establishes a legal violation. Receipts and originals are
    authenticated again by the unchanged analyzers. Incomplete snapshots raise
    SemanticSnapshotError and must not be saved as successfully observed input.
    """
    import re
    from v295_eis_originals import root
    if not re.fullmatch(r"[0-9]{11,20}", str(reg)):
        raise SemanticSnapshotError("invalid_registry_number")
    if con.in_transaction:
        raise SemanticSnapshotError("active_sql_transaction")
    reg = str(reg)
    documents = _semantic_rows(con, "docs", "reg_number", reg,
                               _DOC_FIELDS)
    purchases = _semantic_rows(con, "purchases", "reg_number", reg,
        _CARD_FIELDS + ("source", "url", "card_fields_json"))
    facts = _semantic_rows(con, "v341_card_facts", "reg", reg, ("source_page", "payload"))
    prices = _semantic_rows(con, "v338_card_price", "reg", reg,
        _PRICE_FIELDS + ("source_page", "conflict", "alternative_price"))
    cards = _semantic_cards(purchases, facts, prices, reg)
    io = _SemanticIO()
    data_root = root()
    manifest = _semantic_manifest(io, data_root, reg)
    xml, invalid_xml = _semantic_xml(io, data_root, reg)
    originals = _semantic_originals(io, data_root, reg, documents, manifest, xml)
    payload = {"format": 1, "reg": reg, "cards": cards, "manifest": manifest,
               "xml": xml, "invalid_xml": invalid_xml, "originals": originals}
    try:
        encoded = _canonical(payload)
    except (ValueError, TypeError, RecursionError) as exc:
        raise SemanticSnapshotError("invalid_semantic_value") from exc
    io.charge()
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()



def semantic_source_version(con, reg) -> str:
    """Snapshot sources, protecting one bounded partial pass from LRU churn.

    Other procurements can finish normally but cannot evict the active large
    snapshot's progress. The periodic queues must revisit an incomplete source
    within the six-hour inactivity lease. Completion, a non-budget error or lease
    expiry releases protection; a process restart clears this scheduling cache.
    A continuation whose own metadata exceeds cache caps raises resume_capacity
    rather than looping. No cache result replaces analyzer evidence validation.
    """
    from v295_eis_originals import root
    source = (str(root().absolute()), str(reg))
    _begin_semantic_progress(source)
    try:
        result = _semantic_source_version(con, reg)
    except SemanticSnapshotError as exc:
        _settle_semantic_progress(source, exc)
        raise
    except BaseException:
        _settle_semantic_progress(source)
        raise
    _settle_semantic_progress(source)
    return result
