"""Join a protocol original to publication in its hash-verified EIS XML.

The attachment allow-list is not legal timing evidence. Re-read the saved SOAP
ZIP and XML checksums, and authenticate the actual original before a consumer
can use this date. Notice/revision timestamps, file times and modification
dates are deliberately never substituted for protocol publication.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from pathlib import Path

from v295_eis_originals import MAX_KNOWN_DOCUMENTS, saved_document, uid_from_url

REG = re.compile(r"[0-9]{11,20}$")
HEX = re.compile(r"[0-9a-f]{64}$")


def _publication_day(value):
    raw = str(value or "").strip()
    if not re.fullmatch(r"20\d{2}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?)?", raw):
        return None
    try:
        return (datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
                if "T" in raw else date.fromisoformat(raw)).isoformat()
    except ValueError:
        return None


def _protocol_index(reg):
    # This reader checks both the enclosing ZIP and the exact XML member.
    # Do not read publication dates directly from mutable receipt JSON.
    from v298_eis_only import official_xml_documents
    found = {}
    for record, data in official_xml_documents(reg):
        if record.get("xml_family") != "protocol" or record.get("reg_number") != reg:
            continue
        if not HEX.fullmatch(str(record.get("xml_sha256") or "")):
            continue
        if not HEX.fullmatch(str(record.get("archive_sha256") or "")):
            continue
        # The generic protocol parser deliberately does not interpret XML
        # lot schemas. Do not extend a same-byte alias join across a lot
        # subtree whose binding was dropped from the attachment record.
        lot_urls = {(node.text or "").strip() for group in data.iter()
                    if group.tag.rsplit("}", 1)[-1] in ("lots", "lot", "lotData")
                    for node in group.iter() if node.tag.rsplit("}", 1)[-1] == "url"}
        for attachment in record.get("documents") or []:
            uid = uid_from_url(attachment.get("url") or "")
            if not uid:
                continue
            proof = {key: record.get(key) for key in (
                "xml_type", "xml_guid", "version", "xml_sha256", "xml_filename",
                "archive_sha256")}
            proof.update(attachment_uid=uid, source_url=attachment["url"],
                         attachment_name=attachment.get("name") or "",
                         attachment_scope_unresolved=attachment["url"] in lot_urls,
                         publication=record.get("publication") or "",
                         publication_date=_publication_day(record.get("publication")))
            found.setdefault(uid, []).append(proof)
    return found


def _summarize(uid, candidates):
    unique = {item["xml_sha256"]: item for item in candidates}
    candidates = list(unique.values())
    base = {"family": "protocol", "attachment_uid": uid, "candidates": candidates}
    if not candidates:
        return {**base, "state": "unknown", "reason": "no_verified_protocol_xml"}
    if any(not item["publication_date"] for item in candidates):
        return {**base, "state": "unknown", "reason": "publication_not_explicit"}
    identities = {(item["xml_type"], item["xml_guid"], item["version"],
                   item["publication_date"]) for item in candidates}
    if len(identities) != 1:
        return {**base, "state": "conflict", "reason": "conflicting_protocol_xml_versions_or_dates"}
    if len(candidates) > 1 and not candidates[0]["xml_guid"]:
        return {**base, "state": "unknown", "reason": "multiple_unidentified_protocol_xmls"}
    chosen = candidates[0]
    evidence = ("Публикация протокола в ЕИС: " + chosen["publication_date"] +
                "; официальный XML " + chosen["xml_filename"] +
                "; версия " + str(chosen["version"]) +
                "; UID вложения " + chosen["attachment_uid"] + "; SHA256 XML " + chosen["xml_sha256"] +
                "; SHA256 архива " + chosen["archive_sha256"] + ".")
    return {**base, **chosen, "state": "confirmed", "evidence": evidence}


def annotate_protocol_publications(reg, docs):
    """Keep diagnostic provenance without changing legacy publication fields.

    An archive's timestamp does not identify which member is the protocol.
    Retain parent evidence for review, but never promote it to a member date.
    This saved metadata is descriptive only; the accessor below re-verifies it.
    """
    if not REG.fullmatch(str(reg or "")):
        return
    from v341_precise_legal import matches, SIGNED
    eligible = [doc for doc in docs if doc.get("status") == "checked" and
                len(matches(SIGNED, str(doc.get("text") or ""))) == 1]
    for doc in docs:
        (doc.get("meta") or {}).pop("official_publication_evidence", None)
    if not eligible:
        return
    index = _protocol_index(reg)
    for doc in eligible:
        meta = doc.get("meta") or {}
        uid = uid_from_url(meta.get("source_url") or "")
        if not uid or uid not in index:
            continue
        proof = _summarize(uid, index[uid])
        if meta.get("source") != "official_eis_original":
            proof = {**proof, "state": "unknown",
                     "reason": "archive_member_identity_not_established",
                     "archive_member": meta.get("archive_member"),
                     "archive_sha256": meta.get("archive_sha256")}
            proof.pop("publication_date", None)
        meta["official_publication_evidence"] = proof
        doc["meta"] = meta


def _verified_original_uid(reg, doc):
    """Authenticate a single binary; return its UID or an unknown result."""
    unknown = {"state": "unknown", "family": "protocol"}
    meta = doc.get("meta") or {}
    if not REG.fullmatch(reg) or doc.get("status") != "checked":
        return {**unknown, "reason": "original_not_verified"}
    if meta.get("source") != "official_eis_original":
        return {**unknown, "reason": "archive_member_identity_not_established" if
                meta.get("source") == "official_eis_archive_member" else "original_not_verified"}
    uid = uid_from_url(meta.get("source_url") or "")
    if not uid or (meta.get("uid") and meta["uid"] != uid):
        return {**unknown, "reason": "attachment_identity_mismatch"}
    try:
        saved = saved_document(reg, uid)
        if saved is None:
            return {**unknown, "reason": "original_receipt_missing"}
        path, receipt = saved
        if not doc.get("local") or Path(doc["local"]).resolve(strict=True) != path.resolve(strict=True):
            return {**unknown, "reason": "original_path_mismatch"}
        digest = str(meta.get("sha256") or "")
        if not HEX.fullmatch(digest) or receipt["sha256"] != digest:
            return {**unknown, "reason": "original_hash_mismatch"}
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                return {**unknown, "reason": "original_hash_mismatch"}
        return uid
    except (OSError, ValueError, TypeError, KeyError):
        return {**unknown, "reason": "original_evidence_unavailable"}


def _verified_publication_candidates(purchase, doc, uid, index, originals, family):
    """Reauthenticate same-byte receipt aliases before joining their XML.

    Deduplication labels only identify receipts worth checking. Each alias
    still needs its own same-purchase receipt, exact byte hash and immutable
    family-specific XML. Pool all verified candidates so one convenient alias
    cannot conceal contradictory publication dates or event identities.
    """
    from legal_document_context import document_context, source_scope_context, prepare_context_index
    from v348_cross_document_legal import (_document_event_identity, _protocol_kinds,
                                           protocol_template_title, EXCLUDE)
    reg = str(purchase.get("reg") or purchase.get("reg_number") or "")
    meta = doc.get("meta") or {}
    digest = meta.get("sha256")
    aliases = meta.get("identical_sources") or []
    if not isinstance(aliases, list) or not aliases:
        return list(index.get(uid, [])), None
    verified = {uid: {}}
    if isinstance(aliases, list) and len(aliases) > MAX_KNOWN_DOCUMENTS:
        return [], "alias_history_incomplete"
    seen = {uid}
    body = str(doc.get("text") or "")[:750000]
    context_index = prepare_context_index(body)
    def scope_of(alias, alias_meta):
        return source_scope_context(purchase, {**doc, **alias, "text": body,
            "meta": {**meta, **alias_meta}}, context_index=context_index)
    def context_of(alias, alias_meta):
        return document_context({**doc, **alias, "text": body[:450],
            "meta": {**meta, **alias_meta}})
    if isinstance(aliases, list):
        for alias in aliases:
            if not isinstance(alias, dict):
                continue
            alias_meta = alias.get("meta") if isinstance(alias.get("meta"), dict) else {}
            invalid_metadata = False
            if "metadata_json" in alias:
                try:
                    retained = json.loads(alias["metadata_json"])
                    invalid_metadata = not isinstance(retained, dict) or bool(alias_meta and retained != alias_meta)
                    if not invalid_metadata:
                        alias_meta = retained
                except (ValueError, TypeError):
                    invalid_metadata = True
            url = alias_meta.get("source_url") or alias.get("url") or ""
            alias_uid = uid_from_url(url)
            declared = alias_meta.get("uid") or alias.get("uid")
            if (not alias_uid or alias_uid not in index or (declared and declared != alias_uid)
                    or alias_meta.get("source") in ("official_eis_archive_member", "official_eis_archive")):
                continue
            if alias_uid in seen:
                # Repeated source snapshots retain all explicit scope vetoes.
                if alias_uid in verified:
                    previous = verified[alias_uid]
                    scope = scope_of(alias, alias_meta)
                    prior_scope = scope_of(previous, previous.get("meta") or {})
                    if (invalid_metadata or scope != prior_scope
                            or not context_of(alias, alias_meta)["eligible_actual"]):
                        return [], "alias_event_or_scope_conflict"
                continue
            seen.add(alias_uid)
            saved = saved_document(reg, alias_uid)
            if saved is None or saved[1].get("sha256") != digest:
                continue
            path, _ = saved
            key = (str(path), "official_eis_original", url, digest, alias_uid, "checked")
            if key not in originals:
                originals[key] = _verified_original_uid(reg, {
                    "status": "checked", "local": str(path),
                    "meta": {"source": "official_eis_original", "source_url": url,
                             "uid": alias_uid, "sha256": digest}})
            if originals[key] == alias_uid:
                if invalid_metadata:
                    return [], "alias_event_or_scope_conflict"
                verified[alias_uid] = {**alias, "meta": alias_meta}
    result = []
    canonical_scope = scope_of({}, {})
    canonical_number, canonical_invalid = _document_event_identity(doc, family)
    canonical_kinds = set()
    if family == "PROTOCOL":
        from v341_precise_legal import matches, SIGNED
        canonical_kinds = _protocol_kinds(doc.get("name"), " ".join(match[0] for match in matches(SIGNED, body)))
    for attachment_uid, alias in verified.items():
        for proof in index.get(attachment_uid, []):
            if len(verified) > 1:
                # Metadata can veto a mismatched source, never create its
                # provenance. Event names come from the hash-verified XML.
                alias_doc = {**doc, **alias, "name": proof.get("attachment_name") or doc.get("name"),
                    "text": body, "meta": {**meta, **(alias.get("meta") or {})}}
                scope = source_scope_context(purchase, alias_doc, context_index=context_index)
                number, invalid = _document_event_identity(alias_doc, family)
                template = (protocol_template_title(alias_doc.get("name")) if family == "PROTOCOL"
                            else bool(EXCLUDE.search(str(alias_doc.get("name") or ""))))
                kinds = (canonical_kinds, _protocol_kinds(alias_doc.get("name"), ""))
                if (proof.get("attachment_scope_unresolved")
                        or not document_context({**alias_doc, "text": body[:450]})["eligible_actual"]
                        or not scope["eligible_actual"] or not canonical_scope["eligible_actual"]
                        or any(scope[key] != canonical_scope[key] for key in ("lots", "lot_ids"))
                        or canonical_invalid or invalid or template
                        or (canonical_number and number and canonical_number != number)
                        or (family == "PROTOCOL" and all(kinds) and kinds[0] != kinds[1])):
                    return [], "alias_event_or_scope_conflict"
            result.append(proof)
    return result, None


def protocol_publication_verifier(purchase):
    """Fresh evaluation-scoped verifier with a lazily loaded XML snapshot.

    Each original is hashed at most once and the SOAP corpus at most once in
    one evaluation. No index, stat-based trust or result survives the call to
    the analyzer; the next audit revalidates all evidence from saved bytes.
    """
    reg = str(purchase.get("reg") or purchase.get("reg_number") or "")
    index = None
    originals = {}

    def verify(doc):
        nonlocal index
        meta = doc.get("meta") or {}
        key = (str(doc.get("local") or ""), str(meta.get("source") or ""),
               str(meta.get("source_url") or ""), str(meta.get("sha256") or ""),
               str(meta.get("uid") or ""), str(doc.get("status") or ""))
        if key not in originals:
            originals[key] = _verified_original_uid(reg, doc)
        uid = originals[key]
        if not isinstance(uid, str):
            return dict(uid)
        if index is None:
            index = _protocol_index(reg)
        candidates, conflict = _verified_publication_candidates(
            purchase, doc, uid, index, originals, "PROTOCOL")
        if conflict:
            return {"state": "conflict", "family": "protocol", "reason": conflict}
        return _summarize(uid, candidates)
    return verify


def verified_protocol_publication(purchase, doc):
    """Standalone accessor; never trust caller-supplied publication metadata."""
    return protocol_publication_verifier(purchase)(doc)


def clarification_publication_verifier(purchase):
    """Bind one original to one published clarification/request date chain.

    Reverify the immutable SOAP ZIP/XML and original bytes on every evaluation.
    Unknown/draft status, multiple event identities, conflicting versions and
    malformed/missing dates cannot establish a legal deadline. In particular,
    creationDate/decisionDate never stand in for requestDate/publishDate.
    """
    reg = str(purchase.get("reg") or purchase.get("reg_number") or "")
    index = None
    originals = {}

    def verify(doc):
        nonlocal index
        meta = doc.get("meta") or {}
        key = (str(doc.get("local") or ""), str(meta.get("source") or ""),
               str(meta.get("source_url") or ""), str(meta.get("sha256") or ""),
               str(meta.get("uid") or ""), str(doc.get("status") or ""))
        if key not in originals:
            originals[key] = _verified_original_uid(reg, doc)
        uid = originals[key]
        if not isinstance(uid, str):
            return {**uid, "family": "clarification"}
        if index is None:
            from v298_eis_only import official_xml_documents
            index = {}
            for record, _ in official_xml_documents(reg):
                if record.get("xml_family") != "explanation" or record.get("reg_number") != reg:
                    continue
                if not all(HEX.fullmatch(str(record.get(key) or "")) for key in ("xml_sha256", "archive_sha256")):
                    continue
                for attachment in record.get("documents") or []:
                    attachment_uid = uid_from_url(attachment.get("url") or "")
                    if not attachment_uid:
                        continue
                    item = {key: record.get(key) for key in (
                        "xml_type", "xml_guid", "version", "xml_sha256", "xml_filename", "archive_sha256")}
                    item.update(attachment_uid=attachment_uid, source_url=attachment["url"],
                                attachment_name=attachment.get("name") or "",
                                publication_date=_publication_day(record.get("publication")),
                                request_date=_publication_day(record.get("request_received")),
                                decision_date=_publication_day(record.get("decision")),
                                publication_status=record.get("publication_status"))
                    index.setdefault(attachment_uid, []).append(item)
        candidates, conflict = _verified_publication_candidates(
            purchase, doc, uid, index, originals, "CLARIFICATION")
        if conflict:
            return {"state": "conflict", "family": "clarification", "reason": conflict}
        candidates = list({item["xml_sha256"]: item for item in candidates}.values())
        base = {"family": "clarification", "attachment_uid": uid, "candidates": candidates}
        if not candidates:
            return {**base, "state": "unknown", "reason": "no_verified_clarification_xml"}
        if any(not item["xml_guid"] or item["publication_status"] != "P" or
               not all(item[key] for key in ("request_date", "publication_date", "decision_date"))
               for item in candidates):
            return {**base, "state": "unknown", "reason": "published_clarification_dates_not_explicit"}
        identities = {(item["xml_guid"], item["request_date"], item["publication_date"], item["decision_date"])
                      for item in candidates}
        if len(identities) != 1:
            return {**base, "state": "conflict", "reason": "conflicting_clarification_xml_events_or_dates"}
        chosen = candidates[0]
        if not chosen["request_date"] <= chosen["decision_date"] <= chosen["publication_date"]:
            return {**base, "state": "conflict", "reason": "inconsistent_clarification_date_chain"}
        evidence = ("Запрос разъяснений получен: " + chosen["request_date"] +
                    "; разъяснение опубликовано в ЕИС: " + chosen["publication_date"] +
                    "; официальный XML " + chosen["xml_filename"] +
                    "; GUID разъяснения " + chosen["xml_guid"] + "; UID вложения " + chosen["attachment_uid"] +
                    "; SHA256 XML " + chosen["xml_sha256"] + "; SHA256 архива " + chosen["archive_sha256"] + ".")
        return {**base, **chosen, "state": "confirmed", "evidence": evidence}
    return verify
