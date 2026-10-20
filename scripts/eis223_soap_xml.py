"""Bounded reader for official RI223 purchase XML, not a legal-rule engine.

Names and paths are from the user-supplied 223-FZ format requirements 16.3,
appendix 1 pp.78-79 (clarifications), 2 (notices) and 3 (protocols).
Preserve original bytes; this module
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


class UnsupportedXmlType(ValueError):
    """A well-formed official root whose facts this parser does not support."""

    def __init__(self, kind):
        super().__init__("Unsupported official EIS XML type: " + kind[:80])


class IdentityMismatch(ValueError):
    """A rejected identity, with bounded non-secret diagnostic fields only."""

    def __init__(self,kind,filename_reg,own_number,parent_number,stage):
        super().__init__("Registry number and ZIP filename differ")
        def number(value):
            if not value:return "missing"
            return value if re.fullmatch(r"[0-9]{11,20}(?:-[0-9]{2})?",value) else "invalid"
        self.diagnostic={"stage":stage if stage in ("supported","quarantine") else "unknown",
            "xml_type":kind if kind in NOTICE_TYPES|PROTOCOL_TYPES|{"explanation"} else "unsupported",
            "filename_reg":number(filename_reg),"own_reg":number(own_number),
            "parent_reg":number(parent_number)}


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


def _document_envelope(root, kind, filename):
    """Validate the transport location and filename without interpreting facts."""
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
    return match[1], data


def _unique_child(parent, name):
    if parent is None:
        return None
    nodes = [node for node in parent if local_name(node) == name]
    if len(nodes) > 1:
        raise ValueError("Missing or ambiguous official XML data")
    if nodes and nodes[0].tag not in (name, "{" + PURCHASE_NS + "}" + name):
        raise ValueError("Foreign official XML data namespace")
    return nodes[0] if nodes else None


def _unique_field(parent, name):
    node = _unique_child(parent, name)
    if node is not None and len(node):
        raise ValueError("Missing or ambiguous official XML data")
    return (node.text or "").strip() if node is not None else ""


def _attachment_references(data, kind):
    docs = []
    seen = set()
    unresolved = []
    previous = []
    # Only attachment containers carry downloadable source documents. An
    # arbitrary hyperlink in a criterion/template is not an original file.
    if kind == "explanation":
        container = _unique_child(data, "attachments")
        containers = [] if container is None else [container]
    else:
        containers = (node for node in data.iter() if local_name(node) == "attachments")
    for attachments in containers:
        for document in attachments:
            doc_kind = local_name(document)
            read_field = _unique_field if kind == "explanation" else field
            if kind == "explanation" and document.tag not in (doc_kind, "{" + PURCHASE_NS + "}" + doc_kind):
                raise ValueError("Foreign official XML data namespace")
            document_guid = read_field(document, "guid") or (document.get("guid") or "").strip()
            if doc_kind == "prevDocument":
                if document_guid:
                    previous.append(document_guid)
                else:
                    unresolved.append({"kind": doc_kind, "guid": "", "content_uid": "",
                                       "reason": "previous_document_missing_guid"})
                continue
            if doc_kind not in ("document", "documentFS"):
                continue
            name, url = read_field(document, "fileName"), read_field(document, "url")
            if not url or not name:
                unresolved.append({"kind": doc_kind, "guid": document_guid,
                                   "content_uid": read_field(document, "contentUid"),
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
                         "xml_type": kind, "published": (_unique_field(data, "publishDate") if kind == "explanation"
                                                         else field(data, "publicationDateTime"))})
    return docs, unresolved, previous


def quarantine_document(raw, filename, expected_reg):
    """Retain an unsupported sibling without creating any legal facts or links.

    Only the already documented notice/protocol identity envelopes are allowed.
    This does not extend the supported types: parse_document still rejects this
    member when legal readers later revalidate the immutable archive.
    """
    root = safe_xml(raw)
    kind = local_name(root)
    if root.tag != "{" + PURCHASE_NS + "}" + kind:
        raise ValueError("Not an EIS 223-FZ purchase XML namespace")
    if kind in NOTICE_TYPES | PROTOCOL_TYPES:
        raise ValueError("Expected an unsupported official XML type")
    filename_reg, data = _document_envelope(root, kind, filename)
    if not REG.fullmatch(expected_reg) or filename_reg != expected_reg:
        raise ValueError("Mixed registry numbers in archive")

    def unique_child(parent, name):
        nodes = [node for node in parent if local_name(node) == name]
        if len(nodes) > 1:
            raise ValueError("Missing or ambiguous official XML data")
        if nodes and nodes[0].tag not in (name, "{" + PURCHASE_NS + "}" + name):
            raise ValueError("Foreign official XML data namespace")
        return nodes[0] if nodes else None

    own = unique_child(data, "registrationNumber")
    purchase = unique_child(data, "purchaseInfo")
    parent_reg = unique_child(purchase, "purchaseNoticeNumber") if purchase is not None else None
    own_number = (own.text or "").strip() if own is not None else ""
    purchase_number = (parent_reg.text or "").strip() if parent_reg is not None else ""
    # A protocol may have a document-specific suffix, but it must name the
    # same procurement explicitly. A filename by itself never proves identity.
    if kind == "explanation":
        # Official 223-FZ TFF 16.3, Appendix 1 pp.78-79: explanationData
        # uses the direct required purchaseRegNum, not registrationNumber or
        # purchaseInfo/purchaseNoticeNumber. This is an identity-only exception
        # for this exact documented root; it does not support arbitrary XML or
        # invent legal facts/attachment links from a quarantined document.
        declared = unique_child(data, "purchaseRegNum")
        declared_number = (declared.text or "").strip() if declared is not None else ""
        valid = (declared_number == expected_reg
                 and (own is None or own_number == expected_reg)
                 and (parent_reg is None or purchase_number == expected_reg))
        purchase_number = declared_number
    elif purchase_number:
        valid = purchase_number == expected_reg and (
            own is None or re.fullmatch(re.escape(expected_reg) + r"(?:-[0-9]{2})?", own_number))
    else:
        valid = parent_reg is None and own_number == expected_reg
    if not valid:
        raise IdentityMismatch(kind,filename_reg,own_number,purchase_number,"quarantine")
    # Keep the existing external URL and attachment size validation even
    # though quarantined members never add downloadable references.
    _attachment_references(data, kind)
    return {
        "reg_number": expected_reg, "xml_filename": filename,
        "xml_sha256": hashlib.sha256(raw).hexdigest(), "xml_type": kind,
        "xml_family": "unsupported", "status": "unsupported_xml",
        "reason": "unsupported_xml_type", "documents_referenced": 0,
        "documents": [], "unresolved_attachments": [], "previous_document_guids": [],
    }


def _parse_explanation(raw, filename, root):
    """TFF223 16.3 Appendix 1 pp.78-80, not a generic unknown-XML fallback.

    Interpret documented fields only. Incomplete/edited exports remain in the
    existing quarantine path; duplicate/foreign identities are rejected. The
    dates retain separate meanings: creation is NOT receipt or publication.
    Supporting the format exposes real attachment references to the existing
    download and legal pipeline, not synthetic text or confirmed violations.
    """
    kind = "explanation"
    reg, data = _document_envelope(root, kind, filename)
    quarantine_document(raw, filename, reg)  # independent strict binding

    def scalar(name):
        nodes = [n for n in data if local_name(n) == name]
        if len(nodes) > 1 or (nodes and nodes[0].tag not in (name, "{" + PURCHASE_NS + "}" + name)):
            raise ValueError("Missing or ambiguous official XML data")
        if nodes and list(nodes[0]):
            raise ValueError("Missing or ambiguous official XML data")
        return (nodes[0].text or "").strip() if nodes else ""

    guid = scalar("guid")
    if not re.fullmatch(r"[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}", guid):
        raise UnsupportedXmlType(kind)
    if scalar("status") not in ("", "P"):
        raise UnsupportedXmlType(kind)
    dates = {name: scalar(name) for name in ("requestDate", "creationDate", "decisionDate", "publishDate", "modificationDate")}
    for name, value in dates.items():
        if not value and name == "modificationDate":
            continue
        try:
            if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})?", value):
                raise ValueError("Missing explicit timestamp")
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise UnsupportedXmlType(kind) from None
    docs, unresolved, previous = _attachment_references(data, kind)
    for doc in docs:
        doc["published"] = dates["publishDate"]
    return {
        "reg_number": reg, "xml_filename": filename,
        "xml_sha256": hashlib.sha256(raw).hexdigest(), "xml_type": kind,
        "xml_family": "explanation", "xml_guid": guid, "version": None,
        "publication": dates["publishDate"], "created": dates["creationDate"],
        "modified": dates["modificationDate"], "request_received": dates["requestDate"],
        "decision": dates["decisionDate"], "publication_status": scalar("status"),
        "documents_referenced": len(docs),
        "documents": docs, "unresolved_attachments": unresolved,
        "previous_document_guids": previous,
    }, data


def parse_document(raw, filename):
    root = safe_xml(raw)
    kind = local_name(root)
    if root.tag != "{" + PURCHASE_NS + "}" + kind:
        raise ValueError("Not an EIS 223-FZ purchase XML namespace")
    if kind == "explanation":
        return _parse_explanation(raw, filename, root)
    if kind not in NOTICE_TYPES | PROTOCOL_TYPES:
        raise UnsupportedXmlType(kind)
    filename_reg, data = _document_envelope(root, kind, filename)
    family = "notice" if kind in NOTICE_TYPES else "protocol"
    if family == "notice":
        reg = field(data, "registrationNumber")
    else:
        reg = field(direct(data, "purchaseInfo"), "purchaseNoticeNumber")
    if not REG.fullmatch(reg) or reg != filename_reg:
        raise IdentityMismatch(kind,filename_reg,field(data,"registrationNumber"),
                               field(direct(data,"purchaseInfo"),"purchaseNoticeNumber"),"supported")
    docs, unresolved, previous = _attachment_references(data, kind)
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
