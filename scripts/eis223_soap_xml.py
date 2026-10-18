"""Bounded reader for official RI223 purchase XML, not a legal-rule engine.

Names and paths are from the user-supplied 223-FZ format requirements 16.3,
appendices 2 (notices) and 3 (protocols). Preserve original bytes; this module
only extracts procurement identity and explicit attachment references.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

PURCHASE_NS = "http://zakupki.gov.ru/223fz/purchase/1"
REG = re.compile(r"[0-9]{11,20}$")
NOTICE_TYPES = frozenset((
    "purchaseNotice", "purchaseNoticeOK", "purchaseNoticeOA", "purchaseNoticeZK",
    "purchaseNoticeEP", "purchaseNoticeAE", "purchaseNoticeAE94FZ",
    "purchaseNoticeKESMBO", "purchaseNoticeAESMBO", "purchaseNoticeZPESMBO",
    "purchaseNoticeZKESMBO",
))
# Protocol names are deliberately explicit, rather than trusting arbitrary
# purchaseProtocol* XML as a supported document.
PROTOCOL_TYPES = frozenset("purchaseProtocol" + suffix for suffix in (
    "", "VK", "RZAE", "RZOK", "OSZ", "RZOA", "PAOA", "PAAE",
    "RZ1AE94FZ", "PAAE94FZ", "RZ2AE94FZ", "ZK", "PAEP",
    "FCDKESMBO", "FCODKESMBO", "ZRPZKESMBO", "FKVOKESMBO", "RZ1KESMBO",
    "RZ2KESMBO", "AdditionalCollationKESMBO", "SummingupKESMBO",
    "EvasionKESMBO", "RejectionKESMBO", "CCKESMBO", "ZRPZAESMBO",
    "RZ1AESMBO", "CollationAESMBO", "RZ2AESMBO", "SummingupAESMBO",
    "EvasionAESMBO", "RejectionAESMBO", "CCAESMBO", "ZRPZZKESMBO",
    "RZZKESMBO", "SummingupZKESMBO", "EvasionZKESMBO", "RejectionZKESMBO",
    "CCZKESMBO", "FCDZPESMBO", "FCODZPESMBO", "ZRPZZPESMBO",
    "RZ1ZPESMBO", "RZ2ZPESMBO", "SummingupZPESMBO", "EvasionZPESMBO",
    "RejectionZPESMBO", "CCZPESMBO",
))


def local_name(element):
    return element.tag.rsplit("}", 1)[-1]


def direct(element, name):
    if element is None:
        return None
    return next((child for child in element if local_name(child) == name), None)


def field(element, name):
    child = direct(element, name)
    return (child.text or "").strip() if child is not None else ""


def safe_xml(raw):
    # Removing NULs also detects DTD/entity declarations in UTF-16/32 input.
    # ElementTree never resolves external references; reject internal entities
    # before parsing to avoid expansion of untrusted uploaded XML.
    declaration = raw.replace(b"\x00", b"").upper()
    if b"<!DOCTYPE" in declaration or b"<!ENTITY" in declaration:
        raise ValueError("XML entities are disabled")
    try:
        return ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ValueError("Malformed XML") from exc


def parse_document(raw, filename):
    root = safe_xml(raw)
    kind = local_name(root)
    if root.tag != "{" + PURCHASE_NS + "}" + kind:
        raise ValueError("Not an EIS 223-FZ purchase XML namespace")
    if kind not in NOTICE_TYPES | PROTOCOL_TYPES:
        raise ValueError("Unsupported official EIS XML type: " + kind[:80])
    match = re.fullmatch(
        re.escape(kind) + r"_([0-9]{11,20})(?:-[0-9]{2})?_[A-Za-z0-9_-]{1,160}[.]xml",
        filename,
    )
    if not match:
        raise ValueError("Registry number/type and ZIP filename differ")
    # The official transport envelope is body/item/<type>Data. Historical
    # direct <type>Data fixtures/exports are accepted too, but never multiple
    # competing payloads or a foreign-namespace lookalike.
    data_name = kind + "Data"
    payloads = [node for node in root.iter() if local_name(node) == data_name]
    if len(payloads) != 1:
        raise ValueError("Missing or ambiguous official XML data")
    data = payloads[0]
    if data.tag not in (data_name, "{" + PURCHASE_NS + "}" + data_name):
        raise ValueError("Foreign official XML data namespace")
    # Preserve legacy handling of unqualified payloads under a trusted root.
    # The uploaded TFF specifies the envelope but does not prove a stricter
    # Data elementFormDefault. Do not accept payloads hidden in arbitrary
    # extension/signature/attachment subtrees.
    positions = list(root)
    for body in root:
        if body.tag not in ("body", "{" + PURCHASE_NS + "}body"):
            continue
        for item in body:
            if item.tag in ("item", "{" + PURCHASE_NS + "}item"):
                positions.extend(item)
    if not any(data is candidate for candidate in positions):
        raise ValueError("Official XML data is outside the transport envelope")
    family = "notice" if kind in NOTICE_TYPES else "protocol"
    if family == "notice":
        reg = field(data, "registrationNumber")
    else:
        reg = field(direct(data, "purchaseInfo"), "purchaseNoticeNumber")
    if not REG.fullmatch(reg) or reg != match[1]:
        raise ValueError("Registry number and ZIP filename differ")

    docs = []
    seen = set()
    unresolved = []
    previous = []
    # Only attachment containers carry downloadable source documents. An
    # arbitrary hyperlink in a criterion/template is not an original file.
    for attachments in (node for node in data.iter() if local_name(node) == "attachments"):
        for document in attachments:
            doc_kind = local_name(document)
            document_guid = field(document, "guid") or (document.get("guid") or "").strip()
            if doc_kind == "prevDocument":
                if document_guid:
                    previous.append(document_guid)
                else:
                    unresolved.append({"kind": doc_kind, "guid": "", "content_uid": "",
                                       "reason": "previous_document_missing_guid"})
                continue
            if doc_kind not in ("document", "documentFS"):
                continue
            name, url = field(document, "fileName"), field(document, "url")
            if not url or not name:
                unresolved.append({"kind": doc_kind, "guid": document_guid,
                                   "content_uid": field(document, "contentUid"),
                                   "reason": "attachment_has_no_download_url_or_filename"})
                continue
            try:
                parsed = urlsplit(url)
                valid = (parsed.scheme == "https"
                         and parsed.hostname in ("zakupki.gov.ru", "int.zakupki.gov.ru")
                         and parsed.port in (None, 443)
                         and not parsed.username and not parsed.password and not parsed.fragment)
            except ValueError:
                valid = False
            if not valid:
                raise ValueError("Unexpected document source host")
            if len(name) > 255 or len(url) > 3000:
                raise ValueError("Attachment metadata exceeds limit")
            if url in seen:
                continue
            seen.add(url)
            docs.append({"name": name, "url": url, "guid": document_guid,
                         "xml_type": kind, "published": field(data, "publicationDateTime")})
    version = field(data, "version")
    record = {
        "reg_number": reg, "xml_filename": filename,
        "xml_sha256": hashlib.sha256(raw).hexdigest(), "xml_type": kind,
        "xml_family": family, "xml_guid": field(data, "guid") or (data.get("guid") or "").strip(),
        "version": int(version) if re.fullmatch(r"[0-9]{1,9}", version) else None,
        "publication": field(data, "publicationDateTime"),
        "created": field(data, "createDateTime"),
        "modified": field(data, "modificationDate"),
        "documents_referenced": len(docs), "documents": docs,
        "unresolved_attachments": unresolved, "previous_document_guids": previous,
    }
    return record, data


def revision_key(record):
    """Latest explicit revision, then publication time; never archive hash order."""
    value = record.get("publication") or record.get("modified") or record.get("created")
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        stamp = dt.timestamp()
    except (ValueError, OverflowError):
        stamp = 0
    return (record.get("version") if isinstance(record.get("version"), int) else -1,
            stamp, record.get("xml_sha256", ""))
