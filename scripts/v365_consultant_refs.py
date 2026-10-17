"""Verified ConsultantPlus legal references for original, evidence-backed findings.

Only verified document/article URLs. Never guess a pinpoint law from generic
headlines and never turn a missing legal basis into a confirmed violation.
For verified short statutory clauses, native browser text fragments attempt to
scroll to the precise part. If unsupported, the article still opens correctly.
"""
from __future__ import annotations
import re
from urllib.parse import quote, urlsplit

LAW_223 = "https://www.consultant.ru/document/cons_doc_LAW_116964/"
ARTICLES = {
    "2": "fa59dfcdcfd8c5c80928d33fa840280273464c74",
    "3": "fddec0f5c16a67f6fca41f9e31dfb0dcc72cc49a",
    "3.1-4": "c67fd28cda4a5fef9c5f5286bec5b7e042eeea8f",
    "3.2": "05cd0add21b39d478c8b8af91fb2f2cd80d4a6e8",
    "3.3": "ff87a2a87e0abcae991f7d64b4eb4abd5a6598a6",
    "3.4": "a3b63487ad7d07ce045a07b0f1e7a9d9b064de95",
    "3.5": "c31c00ee3bb67009da75d1cb282e7be109f6f5aa",
    "4": "441d00be62e3224cdc0514cffaf2a26b5b40a1c7",
    "4.1": "b72d7a9ffb1cd85820a27f5f62cc6ca8ca4a21a9",
}
LAW_1352 = "https://www.consultant.ru/document/cons_doc_LAW_172109/6d4f89e8690d09fef93b1c64f4541294c780d052/"
LAW_1875 = "https://www.consultant.ru/document/cons_doc_LAW_494318/"
# Starts of provisions explicitly verified on ConsultantPlus article pages.
PASSAGES = {
    ("3.2", "3"): "В течение трех рабочих дней с даты поступления запроса",
    ("3.2", "4"): "Разъяснения положений документации о конкурентной закупке не должны изменять",
    ("3.2", "6"): "Решение об отмене конкурентной закупки размещается",
    ("4", "8"): "Извещение об осуществлении конкурентной закупки является неотъемлемой частью документации",
    ("4", "9"): "В извещении об осуществлении конкурентной закупки должны быть указаны",
    ("4", "10"): "В документации о конкурентной закупке должны быть указаны",
    ("4", "11"): "Изменения, вносимые в извещение об осуществлении конкурентной закупки",
    ("4", "12"): "Протоколы, составляемые в ходе закупки, размещаются заказчиком",
}
REF_ART = re.compile(
    r"(?:\bст(?:атья|атьи|атье|атью)?\.?\s*)(3\.1-4|4\.1|3\.[1-6]|[2-9])\b",
    re.I,
)
REF_REV = re.compile(
    r"(?:\bч(?:асть|асти|астью)?\.?\s*)(\d+(?:\.\d+)?)\s+"
    r"(?:\bст(?:атьи|атье|атью|атья)?\.?\s*)(3\.1-4|4\.1|3\.[1-6]|[2-9])\b",
    re.I,
)
REF_PART = re.compile(r"\b(?:ч(?:асть|асти|астью)?\.?)\s*(\d+(?:\.\d+)?(?:\s*[–-]\s*\d+)?)", re.I)
REF_POINT = re.compile(r"\b(?:п(?:ункт|ункта|ункте)?\.?)\s*(\d+(?:\.\d+)?)", re.I)


def _normalize(text):
    return re.sub(r"\s+", " ", str(text or "").lower().replace("ё", "е").replace("№", "n")).strip()


def legal_identity(law, code=""):
    """Stable legal act/norm identity for dedup, ignoring punctuation and style.

    Returns the original normalized law when there is no positively identified
    provision, to avoid merging violations under different legal grounds.
    """
    refs = references({"law": law, "code": code})
    if refs:
        return "|".join(sorted(r["identity"] for r in refs))
    return re.sub(r"[^\w\dа-я]+", "", _normalize(law))


def _fragment(url, excerpt):
    return url + "#:~:text=" + quote(excerpt, safe="") if excerpt else url


def references(row):
    """Return verified ConsultantPlus links and precise available provision labels."""
    law = str(row.get("law") or "").strip()
    code = str(row.get("code") or row.get("field_path") or "")
    low = _normalize(law)
    result = []
    seen = set()

    def add(identity, label, url, article=None, part=None):
        if identity in seen:
            return
        seen.add(identity)
        result.append({"identity": identity, "label": label, "url": url,
                       "article": article, "part": part})

    # Match the cited Russian Government resolutions separately: 223-FZ is
    # not a synonym for Resolution 1352 or Resolution 1875.
    if re.search(r"(?<!\d)1352(?!\d)", low):
        m = REF_POINT.search(low)
        if not m and re.search(r"\b1352[-–]28\b", _normalize(code)):
            m = re.search(r"28", "28")
        point = m.group(1) if m else None
        label = "ПП РФ № 1352 · п. " + point if point else "ПП РФ № 1352"
        phrase = "максимальный срок оплаты поставленных товаров" if point == "28" else ""
        add("1352:"+(point or "all"), label, _fragment(LAW_1352, phrase))
    if re.search(r"(?<!\d)1875(?!\d)", low):
        point = REF_POINT.search(low)
        p = point.group(1) if point else None
        label = "ПП РФ № 1875" + (" · п. "+p if p else "")
        add("1875:"+(p or "all"), label, LAW_1875)

    # Multiple article/part citations are independent references; a bare
    # trailing 'ч. 6' inherits the last explicitly cited article in its clause.
    found = list(REF_ART.finditer(low))
    if not found:
        rev = REF_REV.search(low)
        if rev:
            art = rev.group(2)
            found = [rev]  # handled below

    if found:
        for idx,match in enumerate(found):
            if match.re == REF_REV:
                article=match.group(2)
                piece=low[max(0,match.start()-30):match.end()]
                segments=[piece]
            else:
                article=match.group(1)
                after=low[match.end():found[idx+1].start() if idx+1<len(found) else len(low)]
                after=re.split(r"\b(?:пп\s*рф|постановлени[ея]|1875|1352)\b",
                               after,maxsplit=1)[0]
                # A preceding part ("часть 11 статьи 4") belongs to this
                # article, not the previous one.
                prefix=low[max(0,match.start()-48):match.start()]
                before=re.search(
                    r"((?:п(?:ункт|ункта|ункте)?\.?\s*\d+(?:\.\d+)?\s*)?"
                    r"(?:ч(?:асть|асти|астью)?\.?\s*\d+(?:\.\d+)?))\s*$",
                    prefix,re.I)
                segments=[before.group(1)] if before else []
                segments.extend(after.split(";"))
            if article not in ARTICLES:
                continue
            specified=[piece for piece in segments
                       if REF_PART.search(piece) or REF_POINT.search(piece)]
            segments=specified if specified else [""]
            for piece in segments:
                part_match=REF_PART.search(piece)
                point_match=REF_POINT.search(piece)
                part=part_match.group(1).replace(" ","") if part_match else None
                point=point_match.group(1) if point_match else None
                suffix=(" · ч. "+part if part else "")+(" · п. "+point if point else "")
                url=LAW_223+ARTICLES[article]+"/"
                if (article,part) in PASSAGES:
                    url=_fragment(url,PASSAGES[(article,part)])
                identity="223:"+article+":"+(part or "")+":"+(point or "")
                add(identity, "223-ФЗ · ст. "+article+suffix,url,article,part)

    # If a verified code explicitly specifies the same statute but legacy
    # rows have no law text, a code can support identifying the article.
    if not result and not law and code.startswith("223-"):
        m=re.match(r"223-(3\.1-4|3\.2|3\.3|3\.4|4)(?:-([0-9]+))?(?:-|$)",code)
        if m and m.group(1) in ARTICLES:
            article,part=m.groups()
            url = LAW_223+ARTICLES[article]+"/"
            url = _fragment(url,PASSAGES.get((article,part),""))
            add("223:"+article+":"+(part or "")+":",
                "223-ФЗ · ст. "+article+(" · ч. "+part if part else ""),
                url,article,part)
    return result


def consultant_url(url):
    """Only approved ConsultantPlus document URLs can be labelled as law links."""
    try:
        p=urlsplit(str(url or ""))
    except (TypeError,ValueError):
        return False
    return p.scheme=="https" and p.netloc=="www.consultant.ru" and bool(
        re.match(r"^/document/cons_doc_LAW_\d+/",p.path))


def enrich(row):
    """Add fields; keep the original law and evidence untouched."""
    out=dict(row)
    refs=references(out)
    if refs:
        out["consultant_references"] = refs
        out["consultant_url"] = refs[0]["url"]
        out["consultant_citation"] = refs[0]["label"]
        if not out.get("law"):
            out["law"] = refs[0]["label"]
        if not consultant_url(out.get("law_url")):
            out["source_law_url"] = out.get("law_url") or ""
        out["law_url"] = refs[0]["url"]
    elif consultant_url(out.get("law_url")):
        out["consultant_url"] = out["law_url"]
    return out
