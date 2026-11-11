"""Fast, read-only procurement card from durable SQLite evidence.
Installed as the outermost ASGI layer to avoid legacy card re-analysis and
the long chain of card HTML middleware on the critical GET request.
"""
from __future__ import annotations
import asyncio
import html
import re
import sqlite3
from datetime import date
from urllib.parse import urlsplit
from fastapi.responses import HTMLResponse
from v342_identity import organization_display_name
from saved_eis_dates import recovered_date
from account_visual_polish_v1 import card_style

CARD_RE=re.compile(r"^/check-new/([0-9]{10,20})/?$")
STYLE="""<style id="eis-modern-audit-card">
:root{font-family:Inter,"Segoe UI",system-ui,-apple-system,sans-serif;
 color:#294658;background:#f4f7fa;--edge:#dce7ef;--green:#17618f}
*{box-sizing:border-box}
body{margin:0;color:#294657;background:radial-gradient(ellipse at 100% 0%,#eaf3f9,transparent 30%),#f4f7fa}
header{background:#fff;color:#214c6c;border-bottom:1px solid var(--edge);
 padding:18px clamp(16px,4vw,52px)}
header h1{font-size:clamp(19px,2.5vw,25px);letter-spacing:-.03em;margin:0;font-weight:800}
main{max-width:1250px;padding:0 clamp(12px,2.5vw,25px) 42px;margin:22px auto}
nav{display:flex;gap:9px;flex-wrap:wrap;margin-bottom:20px}
a{color:#166092;font-weight:650;text-decoration:none}
a:hover{text-decoration:underline}
nav a,.action{display:inline-flex;align-items:center;justify-content:center;
 min-height:40px;border-radius:10px;padding:10px 14px;
 background:#fff;border:1px solid #d4e3ed;color:#2b6a96;font-weight:700}
.action{background:var(--green);color:white;border-color:var(--green)}
.action:hover{background:#104b74;color:white}
.panel{margin:14px 0;background:white;border:1px solid var(--edge);border-radius:16px;
 padding:clamp(16px,2.5vw,24px);box-shadow:0 7px 22px #123b2b0b}
.panel h2{color:#1f4d70;font-size:clamp(19px,2.2vw,23px);margin:0 0 13px;
 letter-spacing:-.025em}
.card-back{margin:0 0 18px}.card-back a{display:inline-flex;align-items:center;min-height:44px}
.result-group{padding:0;overflow:hidden}
.result-group>summary{cursor:pointer;min-height:56px;padding:18px 22px;
 color:#1f4d70;overflow-wrap:anywhere}
.result-group>summary h2{display:inline;margin:0;font-size:21px}
.result-group>summary::marker{color:#367ba7}
.result-count{font-size:14px;font-weight:650;margin-left:9px;color:#647d70}
.result-body{padding:0 22px 20px}.result-group[open]>summary{border-bottom:1px solid var(--edge);margin-bottom:15px}
.finding-source,.finding-law{line-height:1.65;overflow-wrap:anywhere}
.source-fragment{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.65;
 border-left:3px solid #b8d0df;padding-left:12px}
.card-actions{display:flex;flex-wrap:wrap;gap:10px;align-items:center}
.card-actions a{max-width:100%;white-space:normal;overflow-wrap:anywhere}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:11px 20px}
.legal{border:1px solid #f1d8d8;border-left:4px solid #bf615f;background:#fffafa;
 border-radius:12px;padding:15px 16px;margin:12px 0}
.review{border:1px solid #f0e3c6;border-left:4px solid #b38a38;background:#fffcf4;
 border-radius:12px;padding:13px 15px;margin:10px 0}
.legal h3,.review h3{font-size:16px;margin:0 0 9px;color:#3b4e44}
.evidence{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.65}
.meta{color:#647d70;font-size:13px;line-height:1.6;overflow-wrap:anywhere}
.chip{font-size:12px;border:1px solid #d9e7f0;background:#e9f3fa;color:#316e97;
 border-radius:999px;padding:4px 10px}
table{width:100%;border-collapse:separate;border-spacing:0;border:1px solid #e0e8ee;
 border-radius:12px;overflow:hidden}
th,td{text-align:left;padding:11px 12px;vertical-align:top;overflow-wrap:anywhere;
 border-bottom:1px solid #e9f0f5}
th{background:#eef4f9;color:#426783;font-size:13px}
tbody tr:last-child td{border-bottom:0}
:focus-visible{outline:3px solid #91d9c5;outline-offset:2px}
@media(max-width:660px){
 header{padding:16px 14px}
 main{padding:0 11px 30px;margin:16px auto}
 nav a,.action{flex:1}
 .panel{padding:15px;border-radius:13px}
 .result-group{padding:0}.result-group>summary{padding:16px;min-height:56px}
 .result-group>summary h2{font-size:19px}.result-body{padding:0 15px 16px}
 .legal,.review{padding:13px 12px;min-width:0}
 .grid{grid-template-columns:minmax(0,1fr)}
 table{display:block;overflow-x:auto;-webkit-overflow-scrolling:touch}
 th,td{padding:10px}
}
</style>"""

CARD_EXPERIENCE_STYLE = r"""<style id="eis223-card-experience">
.card-summary{padding:20px!important}
.card-summary-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:16px}
.card-summary .card-eyebrow{font-size:11px;font-weight:600;letter-spacing:.055em;
 color:var(--ui-muted,#636366);text-transform:uppercase;margin:0 0 7px}
.card-summary h2{font-size:clamp(19px,2vw,24px);line-height:1.3;margin:0 0 7px;max-width:900px}
.card-summary-customer{font-size:13px;color:var(--ui-muted,#636366);line-height:1.5;margin:0;overflow-wrap:anywhere}
.card-summary-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;
 margin:18px 0 0;padding-top:15px;border-top:1px solid var(--edge)}
.card-summary-grid div{min-width:0}.card-summary-grid dt{font-size:12px;color:var(--ui-muted,#636366);margin-bottom:4px}
.card-summary-grid dd{font-size:15px;font-weight:600;margin:0;overflow-wrap:anywhere;font-variant-numeric:tabular-nums}
.card-summary-grid .card-summary-price{white-space:normal}
.card-state-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px;
 margin-top:18px;padding-top:15px;border-top:1px solid var(--edge)}
.card-state-grid>div{min-width:0}.card-state-label{font-size:11px;color:var(--ui-muted,#636366);display:block;margin-bottom:5px}
.card-state-grid strong{font-size:13px;font-weight:600;display:block;line-height:1.45}
.card-state-grid .meta{font-size:12px;margin:5px 0 0}
.card-state-grid [data-legal-review-state]{color:var(--ui-ink,#1d1d1f);font-size:13px;font-weight:600;line-height:1.45;margin:0}
.card-result-strip{display:flex;align-items:center;flex-wrap:wrap;gap:9px 22px;margin-top:18px;padding-top:14px;border-top:1px solid var(--edge);font-size:12px}
.card-result-strip>span{display:inline-flex;gap:7px;align-items:baseline}.card-result-strip b{font-size:21px;line-height:1.2;font-weight:600;font-variant-numeric:tabular-nums}
.card-result-strip .card-result-confirmed{color:var(--ui-danger,#b42332)}
.card-result-strip .card-result-review{color:var(--ui-warning,#865b00)}
.card-result-strip .card-result-caption{color:var(--ui-muted,#636366);font-size:11px}
.panel.card-requisites-panel{padding:8px 20px!important}
.card-requisites{padding:0;border:0;background:transparent}
.card-requisites>summary{cursor:pointer;min-height:38px;display:flex;align-items:center;gap:9px;
 color:var(--ui-ink,#1d1d1f);font-weight:600;list-style:none}
.card-requisites>summary::-webkit-details-marker{display:none}
.card-requisites>summary:before{content:'›';font-size:20px;color:var(--ui-muted,#636366);transition:transform .15s}
.card-requisites[open]>summary:before{transform:rotate(90deg)}
.card-requisites[open]>summary{margin-bottom:14px}
.card-requisites .grid{gap:14px 22px}.card-requisites .grid>div{min-width:0;overflow-wrap:anywhere;font-size:13px}
.card-documents{table-layout:auto}.card-documents td:first-child{font-weight:500}
.card-documents td:last-child a{display:inline-flex;align-items:center;min-height:32px}
@media(max-width:660px){
 .card-summary{padding:16px!important}.card-summary h2{font-size:19px}
 .card-summary-grid{gap:14px;margin-top:14px;padding-top:12px}
 .card-summary-grid dd{font-size:14px}
 .card-state-grid{grid-template-columns:1fr;gap:12px;margin-top:14px;padding-top:12px}
 .card-state-grid>div{display:grid;grid-template-columns:76px minmax(0,1fr);column-gap:12px;align-items:start}
 .card-state-grid .card-state-label{grid-row:1/3;padding-top:1px}
 .card-state-grid .meta{grid-column:2}.card-state-grid strong{grid-column:2}
 .card-result-strip{margin-top:14px;padding-top:12px;gap:8px 16px}
 .card-result-strip .card-result-caption{flex-basis:100%}
 .card-requisites .grid{grid-template-columns:1fr;gap:12px}
 .panel.card-requisites-panel{padding:8px 15px!important}
 table.card-documents{display:block;max-width:100%;overflow:visible;border:0;background:transparent;border-radius:0}
 .card-documents thead{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap}
 .card-documents tbody{display:block}
 .card-documents tr{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:10px 14px;
  border-bottom:1px solid var(--edge);padding:14px 0;background:transparent}
 .card-documents tr:first-child{padding-top:0}.card-documents tr:last-child{border-bottom:0;padding-bottom:0}
 .card-documents td,.card-documents th{display:block;min-width:0!important;width:auto;border:0;padding:0;font-size:13px;line-height:1.5;overflow-wrap:anywhere}
 .card-documents td:first-child,.card-documents td:last-child{grid-column:1/-1}
 .card-documents td:first-child{font-weight:600}
 .card-documents td:not(:first-child):before{content:attr(data-label);display:block;font-size:11px;font-weight:400;color:var(--ui-muted,#636366);margin-bottom:3px}
 .card-documents td:last-child{display:flex;justify-content:space-between;align-items:center;gap:10px}
 .card-documents td:last-child:before{margin:0}
 .card-documents td:last-child a{min-height:40px;border:1px solid var(--edge);border-radius:8px;padding:7px 11px}
}
@media(pointer:coarse){.card-requisites>summary,.card-documents td:last-child a{min-height:44px}}
@media(prefers-reduced-motion:reduce){.card-requisites>summary:before{transition:none}}
</style>"""

def esc(x):
    return html.escape(str(x if x is not None else ""), quote=True)

def source_link(url, title):
    try:
        raw=str(url or "")
        if re.search(r"[\s\\\x00-\x1f\x7f]",raw):return ""
        p=urlsplit(raw)
        if p.scheme not in ("https", "http") or not p.hostname:
            return ""
        if p.username or p.password or p.port not in (None,443 if p.scheme=="https" else 80):
            return ""
        if p.hostname != "zakupki.gov.ru" and not p.hostname.endswith(".zakupki.gov.ru"):
            return ""
    except (ValueError,TypeError):
        return ""
    return '<a href="'+esc(url)+'" target="_blank" rel="noopener noreferrer">'+esc(title)+'</a>'

def text(x):
    return esc(x) if x not in (None,"") else "Не указано"

def val(p,key):
    return p.get(key) if p is not None else None


def display_date(value):
    """Format only a saved ISO date prefix; retain its original time/zone."""
    if value is None:return None
    raw=str(value)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[T ].*)?",raw):return value
    try:
        saved=date.fromisoformat(raw[:10])
    except ValueError:
        return value
    suffix=raw[10:]
    if suffix.startswith("T"):suffix=" "+suffix[1:]
    return saved.strftime("%d.%m.%Y")+suffix


def saved_count(value):
    """A missing or malformed display count is unknown, rather than zero."""
    if value is None or isinstance(value,bool):return None
    raw=str(value).strip()
    return int(raw) if re.fullmatch(r"[0-9]+",raw) else None


def document_progress(p,dtotal):
    checked=saved_count(val(p,"documents_checked"))
    found=saved_count(val(p,"documents_found"))
    if checked is None:
        label="Число проверенных документов не указано"
    elif found is None:
        label="Проверено документов: "+str(checked)+"; общее число не указано"
    else:
        label="Проверено "+str(checked)+" из "+str(found)
    saved=saved_count(dtotal)
    note=("В сохранённой записи: "+str(saved) if saved is not None
          else "Число сохранённых документов не указано")
    return ('<div data-eis-status="documents"><span class="card-state-label">Документы</span>'
            '<strong>'+esc(label)+'</strong><p class="meta">'+esc(note)+'</p></div>')


def compact_summary(reg,p,confirmed,review,dtotal,legal_review,has_previous,thematic=None):
    title=val(p,"title") or "Сведения о закупке пока не сохранены"
    customer=organization_display_name(val(p,"customer")) if p else ""
    price=val(p,"price")
    if price in (None,""):price=val(p,"price_num")
    deadline=display_date(recovered_date(p,"submission_end")) if p else None
    if thematic:
        names={"queued":"В очереди","completed":"Проверено",
          "waiting_originals":"Ожидаются оригиналы ЕИС",
          "waiting_engine":"Ожидается обработчик","retry":"Повторная попытка",
          "excluded_company":"Форма заказчика не соответствует выборке"}
        state=thematic.get("state") or "queued"
        audit_notice='<p class="meta" data-legal-review-state="1">Тематическая проверка: '+esc(names.get(state,"Состояние не указано"))+'</p>'
    else:
        audit_notice=legal_state_notice(legal_review,has_previous)
    review_label="Пересмотрено" if thematic else "Требует проверки"
    return ('<section class="panel card-summary" data-eis-section="summary" data-eis-reg="'+esc(reg)+'">'
        '<div class="card-summary-heading"><div><p class="card-eyebrow">'+
        ('Тематическая проверка' if thematic else 'Проверка закупки')+' · '+esc(reg)+'</p>'
        '<h2 data-eis-summary="title">'+esc(title)+'</h2>'+
        ('<p class="card-summary-customer" data-eis-summary="customer">'+esc(customer)+'</p>' if customer else '')+
        '</div></div><dl class="card-summary-grid"><div><dt>Стоимость закупки</dt>'
        '<dd class="card-summary-price" data-eis-summary="price">'+text(price)+'</dd></div>'
        '<div><dt>Окончание подачи заявок</dt><dd data-eis-summary="deadline">'+text(deadline)+'</dd></div></dl>'
        '<div class="card-state-grid">'+document_progress(p,dtotal)+
        '<div data-eis-status="legal"><span class="card-state-label">Проверка</span>'+audit_notice+'</div></div>'
        '<div class="card-result-strip"><span class="card-result-caption">В сохранённых результатах</span>'
        '<span>Подтверждено <b data-eis-count="confirmed"'+(' class="card-result-confirmed"' if confirmed else '')+'>'+str(confirmed)+'</b></span>'
        '<span>'+review_label+' <b data-eis-count="review"'+(' class="card-result-review"' if review else '')+'>'+str(review)+'</b></span>'
        '</div></section>')

def recovered_submission_start(p):
    """Recover a missing start date only from an explicit saved official-card field.

    Never infer it from publication date or bid deadline. Preserve the source
    timezone label rather than silently converting a local EIS date to Moscow.
    """
    from saved_eis_dates import recovered_date
    return recovered_date(p,"submission_start")

def read(ns,reg):
    c=ns["conn"]()
    try:
        c.row_factory=sqlite3.Row
        c.execute("PRAGMA busy_timeout=1200")
        c.execute("PRAGMA query_only=ON")
        p=c.execute("SELECT * FROM purchases WHERE reg_number=?",(reg,)).fetchone()
        purchase=dict(p) if p else None
        findings=[dict(r) for r in c.execute(
          """SELECT *
             FROM findings WHERE reg_number=? AND kind='violation'
             ORDER BY id DESC LIMIT 350""",(reg,))]
        checks=[dict(r) for r in c.execute(
          """SELECT *
             FROM checks WHERE reg_number=? AND status IN ('violation','check')
             ORDER BY id DESC LIMIT 350""",(reg,))]
        from payment_finding_reassessment import apply_general_reassessments
        findings=apply_general_reassessments(c,reg,findings)
        checks=apply_general_reassessments(c,reg,checks)
        docs=[dict(r) for r in c.execute(
          """SELECT name,doc_type,status,url,text_chars FROM docs WHERE reg_number=?
             ORDER BY id DESC LIMIT 80""",(reg,))]
        ftotal=c.execute("SELECT COUNT(*) FROM findings WHERE reg_number=? AND kind='violation'",(reg,)).fetchone()[0]
        ctotal=c.execute("SELECT COUNT(*) FROM checks WHERE reg_number=? AND status='violation'",(reg,)).fetchone()[0]
        dtotal=c.execute("SELECT COUNT(*) FROM docs WHERE reg_number=?",(reg,)).fetchone()[0]
        return purchase,findings,checks,docs,ftotal,ctotal,dtotal
    finally:
        c.close()

def intake_legal_state(ns,reg):
    """Read-only fallback for original-intake jobs outside the general queue."""
    reader=ns.get("_v295_review_state")
    if not callable(reader):return {}
    try:
        value=reader(reg)
        if not isinstance(value,dict) or value.get("reg_number")!=reg:return {}
        state=str(value.get("status") or "unknown")
        if state=="not_queued":return {}
        if state in ("partial","waiting_for_originals"):
            state="failed" if value.get("retry_exhausted") else "pending"
        documents=max(0,int(value.get("checked_documents") or 0))
        return {"state":state,"documents_read":documents}
    except (OSError,ValueError,TypeError,OverflowError):
        return {"state":"unknown","documents_read":0}


def read_legal_state(ns,reg):
    """Optional, read-only queue metadata; keep read()'s seven-value contract."""
    c=ns["conn"]()
    audit={}
    try:
        c.row_factory=sqlite3.Row
        c.execute("PRAGMA busy_timeout=1200")
        c.execute("PRAGMA query_only=ON")
        if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='v332_audit_queue'").fetchone():
            row=c.execute("SELECT state,documents_read FROM v332_audit_queue "
                          "WHERE reg=? ORDER BY updated_at DESC LIMIT 1",(reg,)).fetchone()
            audit=dict(row) if row else {}
    finally:
        c.close()
    # The original-intake state reader touches a small bounded JSON record;
    # close every SQLite reader before touching the filesystem.
    return audit or intake_legal_state(ns,reg)


def legal_state_notice(audit,has_previous):
    state=(audit or {}).get("state")
    if state in ("pending","queued"):
        label="Юридическая проверка в очереди"
    elif state in ("running","processing"):
        label="Идёт юридическая проверка"
    elif state in ("failed","error"):
        label="Ошибка юридической проверки"
    elif state=="no_readable_documents":
        label="Не удалось прочитать документы для юридической проверки"
    elif state=="completed":
        label=("Юридическая проверка завершена" if int(audit.get("documents_read") or 0)>0
               else "Юридическая проверка не завершена: нет проверенных документов")
    else:
        label="Юридическая проверка ещё не завершена"
    note='<p class="meta" data-legal-review-state="1">'+esc(label)+'</p>'
    if has_previous and state in ("pending","queued","running","processing","failed","error"):
        note+=('<p class="meta" data-previous-results="1">Показаны сохранённые результаты '
               'предыдущей проверки. Новая проверка ещё не завершена.</p>')
    return note


def verified_legal_references(item):
    """Use the existing verified catalog only for the explicitly cited act."""
    from v365_consultant_refs import references
    record=dict(item or {})
    law=str(record.get("law") or "")
    if law and not re.search(r"(?<!\d)(?:223|1352|1875)(?!\d)",law):
        return []
    refs=references(record)
    if law and not re.search(r"(?<!\d)223(?!\d)",law):
        refs=[ref for ref in refs if not ref["identity"].startswith("223:")]
    return refs


def safe_law_url(link):
    from v365_consultant_refs import consultant_url
    raw=str(link or "")
    return not re.search(r"[\s\\\x00-\x1f\x7f]",raw) and consultant_url(raw)


def legal_links(item, law=None, link=None):
    """Build only verified ConsultantPlus law links; never use EIS URL filter."""
    record={**(item or {}),"law":law or (item or {}).get("law"),
            "law_url":link or (item or {}).get("law_url")}
    refs=verified_legal_references(record)
    if refs:
        return '<p class="finding-law"><b>Применённая норма:</b> '+'; '.join(
          '<a href="'+esc(ref["url"])+'" target="_blank" rel="noopener noreferrer">'+
          esc(ref["label"])+' · КонсультантПлюс ↗</a>' for ref in refs)+'</p>'
    if safe_law_url(link):
        return ('<p class="meta"><b>Источник нормы:</b> <a href="'+esc(link)+
                '" target="_blank" rel="noopener noreferrer">'+
                'КонсультантПлюс · открыть норму ↗</a></p>')
    return '<p class="meta">Точная часть или пункт статьи требуют юридического уточнения.</p>'


def linked_evidence(value):
    """Keep the stored wording; make only already cited EIS originals clickable."""
    result=[]
    cursor=0
    for match in re.finditer(r"https?://[^\s<>\"«»]+",str(value or "")):
        url=match.group(0).rstrip(".,;:)]}")
        result.append(esc(str(value or "")[cursor:match.start()]))
        result.append(source_link(url,url) or esc(url))
        cursor=match.start()+len(url)
    result.append(esc(str(value or "")[cursor:]))
    return ''.join(result)


def presentation_gaps(item):
    """Conservative display safeguard, never a new legal decision or DB update."""
    evidence=str(item.get("evidence") or "").strip()
    normalized=re.sub(r"[\s.!]+"," ",evidence.casefold()).strip()
    title=re.sub(r"[\s.!]+"," ",str(item.get("title") or item.get("item") or "").casefold()).strip()
    placeholders={"не указано","нет данных","данные отсутствуют","основание не заполнено",
                  "основание отсутствует","доказательства отсутствуют","документально установлено"}
    missing=[]
    unavailable=re.match(r"(?:не удалось (?:получить|загрузить|извлечь|прочитать)|"
                         r"(?:оригиналы?|документы?|данные|сведения) (?:пока )?"
                         r"(?:не (?:получены?|сохранены?|загружены?)|отсутствуют))",normalized)
    declaration=re.fullmatch(r"(?:разъяснение|ответ|протокол|извещение|решение) "
                             r"(?:опубликован[оы]?|размещ[её]н[оы]?) "
                             r"(?:с нарушением срока|позднее установленного срока)",normalized)
    if not evidence or normalized in placeholders or unavailable or declaration or normalized==title:
        missing.append("не сохранено описание конкретного факта")
    # A missing optional URL/location must not overrule an analyzer's factual
    # finding. The original's document name and literal evidence can still be
    # valid; show a link warning separately, without reclassifying that record.
    if not str(item.get("law") or "").strip() and not verified_legal_references(item):
        missing.append("не сохранена применённая норма")
    return missing


def result_accordion(key,label,count,body):
    # Native details/summary supplies click, Enter and Space handling, exposes
    # its state to assistive technology, and works without JavaScript.
    section="findings" if key=="audit-violations" else "review"
    return ('<details class="panel result-group" id="'+esc(key)+'" data-eis-section="'+section+'">'
            '<summary><h2>'+esc(label)+'</h2><span class="result-count">'+str(count)+
            '</span></summary><div class="result-body">'+body+'</div></details>')


def detail_block(item, kind, title, law, evidence, document, source, link, review_reg=None, review_batch=""):
    review_control=""
    if review_reg and kind=="legal":
        from scripts.v382_finding_review_ui import finding_control
        review_control=finding_control(review_reg,item,review_batch)
    supporting=(item or {}).get("supporting_sources") or []
    extra=""
    if len(supporting)>1:
        labels=[]
        for origin in supporting[:20]:
            name=origin.get("document") or "Оригинал ЕИС"
            target=source_link(origin.get("url"),name)
            if target and target not in labels:labels.append(target)
        if labels:
            extra='<p class="meta"><b>Подтверждения в оригиналах ЕИС:</b> '+(
                '; '.join(labels))+'</p>'
    reassessment=(item or {}).get("reassessment")
    revised=(item or {}).get("effective_status","confirmed")!="confirmed"
    heading=('<p class="meta"><b>'+('Первоначальный вывод — пересмотрен' if revised else 'Карточка нарушения')+'</b></p>' if kind=="legal" else "")
    if reassessment:
        heading+='<p class="meta msp-reassessment"><b>Перепроверка применимости правил МСП:</b> '+esc(reassessment.get("reason"))+\
            ' <span>Дата: '+esc(reassessment.get("created_at"))+'</span></p>'
    origin=source_link(source,document or "Оригинал ЕИС")
    source_html=('<p class="finding-source"><b>Источник:</b> '+(origin or esc(document))+
                 ('. '+esc((item or {}).get("location")) if (item or {}).get("location") else '')+
                 '</p>') if origin or document else ''
    fragment=(item or {}).get("match_text")
    note=(item or {}).get("note")
    gaps=(item or {}).get("presentation_gaps") or []
    missing_link=(not origin and not any(source_link(x.get("url"),"ЕИС")
                    for x in supporting if isinstance(x,dict)))
    # Findings already display their verified citation under «Применённая норма».
    # Keep the stored law visible when no verified citation can be resolved,
    # without a second, duplicate «Правовое основание» row.
    basis_html=('<p><b>Правовое основание:</b> '+esc(law)+'</p>' if law else '')
    if kind=="legal":
        refs=verified_legal_references({**(item or {}),"law":law or (item or {}).get("law"),
                                       "law_url":link or (item or {}).get("law_url")})
        basis_html=('<p class="finding-law"><b>Применённая норма:</b> '+esc(law)+'</p>'
                    if law and not refs else '')
    return ('<article class="'+kind+'">'+heading+'<h3>'+esc(title or "Нарушение")+'</h3>'
      +('<p class="evidence"><b>Факты и обстоятельства:</b> '+linked_evidence(evidence)+'</p>' if evidence else '')
      +basis_html
      +legal_links(item,law,link)
      +source_html
      +('<p class="meta">Проверяемая ссылка на оригинал ЕИС в сохранённой записи отсутствует. '
        'Сверьте указанный документ и приведённый фрагмент.</p>' if missing_link else '')
      +('<p class="source-fragment"><b>Фрагмент оригинала:</b> '+esc(fragment)+'</p>' if fragment else '')
      +('<p class="meta"><b>Пояснение проверки:</b> '+esc(note)+'</p>' if note else '')
      +('<p class="meta"><b>Почему требует проверки:</b> '+esc('; '.join(gaps))+'. '
        'В этой карточке запись не включена в подтверждённые нарушения. Сохранённая запись не изменена.</p>' if gaps else '')
      +extra+review_control
      +'</article>')

def render_thematic_panel(reg,thematic,account_review=False):
    """Reuse the same panels and finding cards used by the main audit view."""
    if not thematic:return ""
    batch=thematic["batch"]
    from v352_dashboard_truth import collapse_legal_events
    rows=(list(thematic.get("findings",[])) if thematic.get("findings_canonical")
          else collapse_legal_events(thematic.get("findings",[])))
    labels=thematic.get("category_labels") or {}
    selected=thematic.get("categories") or []
    state=thematic.get("state") or "queued"
    names={"queued":"В очереди","completed":"Проверено",
      "waiting_originals":"Ожидаются оригиналы ЕИС",
      "waiting_engine":"Ожидается обработчик","retry":"Повторная попытка",
      "excluded_company":"Форма заказчика не соответствует выборке"}
    result=['<section class="panel" id="thematic-results" data-eis-section="findings" data-eis-audit="thematic">',
      '<h2>Нарушения: '+str(sum(item.get("effective_status","confirmed")=="confirmed" for item in rows))+'</h2>',
      '<div class="grid"><div><b>Состояние проверки:</b> '+esc(names.get(state,state))+
      '</div><div><b>Выбранные направления:</b> '+
      esc(', '.join(labels.get(c,c) for c in selected) or "Не указаны")+
      '</div></div>']
    if thematic.get("detail"):
        result.append('<p class="meta">'+esc(thematic["detail"])+'</p>')
    revised=sum(item.get("effective_status","confirmed")!="confirmed" for item in rows)
    if revised:
        result.append('<p class="meta"><b>Пересмотренные выводы / требуют проверки: '+str(revised)+
                      '.</b> Первоначальные доказательства и решения руководителя сохранены ниже.</p>')
    if account_review:
        from scripts.account_memo_requisites_ui_v1 import render_memo_requisites_editor
        result.append(render_memo_requisites_editor(reg))
    if any(item.get("effective_status","confirmed")=="confirmed" for item in rows):
        result.append('<p><a class="action" href="/api/v304/thematic-memo/'+esc(batch)+'/'+
          esc(reg)+'.odt">Сформировать служебную записку ODT</a></p>')
        result.append('<p class="meta">Проект формируется по сохранённым тематическим нарушениям; '+
          'перед подписанием необходима юридическая сверка с оригиналами ЕИС.</p>')
    else:
        result.append('<p>Подтверждённых нарушений по выбранным направлениям пока нет. '+
          'Закупка может ещё ожидать проверки документов.</p>')
    for item in rows:
        category=labels.get(item.get("category"),item.get("category") or "Без категории")
        block=detail_block(item,"legal",item.get("title"),item.get("law"),
            item.get("evidence"),item.get("document"),
            item.get("source_url"),item.get("law_url"),
            review_reg=reg if account_review else None,review_batch=batch)
        result.append(block.replace('<article class="legal">',
              '<article class="legal"><p class="meta"><b>Номер закупки ЕИС: '+esc(reg)+
              '</b></p><p class="meta">Категория: '+
              esc(category)+'</p>',1))
    result.append('</section>')
    return ''.join(result)

def render(reg,data,thematic=None,account_review=False,legal_review=None):
    p,findings,checks,docs,ftotal,ctotal,dtotal=data
    from v333_dedup_ui import collapse
    from v352_dashboard_truth import collapse_legal_events
    candidates=[dict(f) for f in findings]
    candidates.extend({**c, "title":c.get("item") or "Нарушение"}
                      for c in checks if c.get("status")=="violation")
    grouped=collapse_legal_events(candidates)
    incomplete=[]
    unique=[]
    for item in grouped:
        gaps=presentation_gaps(item)
        gaps=list(dict.fromkeys([*gaps,*(item.get("presentation_gaps") or [])]))
        if item.get("effective_status","confirmed") != "confirmed" and not gaps:
            gaps=["Сохранённое условие оплаты требует повторной проверки применимости договора и редакции."]
        if gaps:
            incomplete.append({**item,"item":item.get("title"),"status":"check",
                               "presentation_gaps":gaps})
        else:
            unique.append(item)
    violation=[detail_block(item,"legal",item.get("title"),item.get("law"),
          item.get("evidence"),item.get("document"),item.get("source_url"),
          item.get("law_url"),review_reg=reg if account_review and not thematic else None) for item in unique]
    reviews=collapse(incomplete+[dict(x) for x in checks if x.get("status")=="check"],field="item")
    # Display counts must agree with the number of distinct cards. Historical
    # findings and checks remain untouched and available in the ledger.
    status=(("Нарушения общей проверки: " if thematic else "Подтверждённые нарушения: ")+str(len(violation)))
    basic=''
    if p:
        keys=[
          ('Предмет закупки',val(p,'title')),('Заказчик',organization_display_name(val(p,'customer'))),
          ('ИНН заказчика',val(p,'customer_inn')),('Способ закупки',val(p,'method')),
          ('Стоимость закупки',val(p,'price')),('Дата размещения',display_date(recovered_date(p,'published'))),
          ('Начало подачи заявок',display_date(recovered_submission_start(p))),
          ('Окончание подачи заявок',display_date(recovered_date(p,'submission_end')))]
        if not val(p,'price') and val(p,'price_num') is not None:
            keys[4]=('Стоимость закупки',val(p,'price_num'))
        from v383_msp_scope import display as msp_display
        try:
            from scripts.v385_msp_reassessment import scope_display
        except ModuleNotFoundError as exc:
            if exc.name != "scripts":raise
            from v385_msp_reassessment import scope_display
        current_msp=scope_display((thematic or {}).get('findings') or [])
        keys.append(('Закупка только для МСП',current_msp or msp_display(p)))
        basic='<div class="grid">'+''.join('<div><b>'+esc(k)+':</b> '+text(v)+'</div>' for k,v in keys)+'</div>'
        eis=source_link(p.get('url'),'Открыть карточку ЕИС')
        if eis:basic+='<p>'+eis+'</p>'
    else:
        basic='<p>Карточка закупки пока не сохранена. После получения сведений ЕИС она появится здесь автоматически. Ранее найденные нарушения, если они есть в базе, показаны ниже.</p>'
    has_previous=bool(findings or checks or (saved_count(val(p,'documents_checked')) or 0))
    summary_confirmed,summary_review=len(violation),len(reviews)
    if thematic:
        thematic_rows=(list(thematic.get("findings",[])) if thematic.get("findings_canonical")
                       else collapse_legal_events(thematic.get("findings",[])))
        summary_confirmed=sum(item.get("effective_status","confirmed")=="confirmed" for item in thematic_rows)
        summary_review=sum(item.get("effective_status","confirmed")!="confirmed" for item in thematic_rows)
    summary=compact_summary(reg,p,summary_confirmed,summary_review,dtotal,legal_review,has_previous,thematic)
    # The shared account shell is injected only on the homepage, not this
    # read-only card. Keep a single quiet return path, not three duplicate
    # button-style section links; the sections remain in the main navigation.
    nav='<p class="card-back"><a href="/">← На главную</a></p>'
    if thematic:
        nav=('<nav><a href="/thematic#'+esc(thematic["batch"])+
             '">← К тематической очереди</a></nav>')
    thematic_html=render_thematic_panel(reg,thematic,account_review=account_review)
    export_label=('Скачать ODT сохранённых результатов · требуется сверка' if incomplete
                  else 'Сформировать служебную записку ODT')
    actions='<p class="card-actions"><a class="action" href="/api/v304/memo/'+esc(reg)+'.odt">'+export_label+'</a></p>' if any(item.get("effective_status","confirmed")=="confirmed" for item in candidates) else '<p class="meta">Служебная записка формируется при наличии подтверждённых нарушений.</p>'
    if thematic:actions=''
    inspection_actions=('<p class="card-actions"><a class="action" href="/api/inspection/'+esc(reg)+
                        '.odt">Лист внеплановой проверки ODT</a></p>')
    checked='<p class="meta">'+str(len(reviews))+' вопросов требуют дополнительной юридической проверки.</p>'
    for x in reviews[:50]:
        checked+=detail_block(x,'review',x.get('item'),x.get('law'),x.get('evidence'),x.get('document'),x.get('source_url'),x.get('law_url'))
    if len(reviews)>50:checked+='<p>Показаны первые 50 из '+str(len(reviews))+' вопросов для проверки.</p>'
    # Explicit native roles survive the mobile record-grid presentation.
    docs_html='<table class="card-documents" role="table" aria-label="Документы закупки"><thead role="rowgroup"><tr role="row"><th role="columnheader">Документ</th><th role="columnheader">Вид</th><th role="columnheader">Состояние</th><th role="columnheader">Источник</th></tr></thead><tbody role="rowgroup">'
    for d in docs:
        docs_html+='<tr role="row"><td role="cell" data-label="Документ">'+text(d.get('name'))+'</td><td role="cell" data-label="Вид">'+text(d.get('doc_type'))+'</td><td role="cell" data-label="Состояние">'+text(d.get('status'))+'</td><td role="cell" data-label="Источник">'+source_link(d.get('url'),'Открыть в ЕИС')+'</td></tr>'
    docs_html+='</tbody></table>'
    if dtotal>len(docs):docs_html+='<p class="meta">Показаны последние '+str(len(docs))+' из '+str(dtotal)+' документов.</p>'
    if not docs:docs_html='<p>Оригиналы документов пока не сохранены.</p>'
    if ftotal+ctotal>len(grouped):
        notes=('<p class="meta">Уникальных правовых эпизодов: '+str(len(violation))+
               '. Исходных подтверждённых записей: '+str(ftotal+ctotal)+
               '. Повторы объединены только в интерфейсе; доказательства сохранены в базе.</p>')
    else:notes=''
    if incomplete:
        notes+=('<p class="meta" data-evidence-gap-notice="1">Сохранённых записей с отметкой '
                '«нарушение»: '+str(ftotal+ctotal)+'. В разделе «Требует проверки» показано '
                +str(len(incomplete))+' эпизодов с неполными доказательствами. '
                'Число подтверждённых нарушений в этой карточке: '+str(len(violation))+'. '
                'Статусы и счётчики в базе не изменены; выгрузка ODT использует сохранённые результаты, '
                'поэтому эти эпизоды необходимо сверить перед подписанием.</p>')
    # The thematic card is its own audit. Never append general findings or
    # general "review" cards underneath the thematic violations.
    if notes:notes='<section class="card-result-notices" data-eis-section="notices">'+notes+'</section>'
    general_section=('' if thematic else actions+notes+
        result_accordion('audit-violations','Нарушения',len(violation),
        '<p class="meta">'+esc(status)+'</p>'+(''.join(violation) if violation else
        '<p>Подтверждённых нарушений в сохранённых результатах пока нет. '
        'Это не означает, что нарушений не существует.</p>')))
    review_section='' if thematic else result_accordion('audit-review','Требует проверки',len(reviews),checked)
    review_assets=""
    if account_review:
        from scripts.v382_finding_review_ui import STYLE as review_style, SCRIPT as review_script
        review_assets=review_style+review_script
    return ('<!doctype html><html lang="ru"><head><meta charset="utf-8">'
     '<meta name="viewport" content="width=device-width,initial-scale=1">'
     '<title>'+('Тематическая проверка ' if thematic else 'Карточка проверки ')+esc(reg)+'</title>'+STYLE+card_style()+CARD_EXPERIENCE_STYLE+'</head><body>'
     '<header><h1>'+('Карточка тематической проверки № ' if thematic else 'Карточка проверки закупки № ')+esc(reg)+'</h1></header><main>'+nav
     +summary
     +'<section class="panel card-requisites-panel" data-eis-section="overview"><details class="card-requisites"><summary>Сведения о закупке</summary>'+basic+'</details></section>'
     +inspection_actions
     +thematic_html
     +general_section+review_section
     +'<section class="panel" data-eis-section="documents"><h2>Документы ЕИС ('+str(dtotal)+')</h2>'+docs_html+'</section>'
     +'</main>'+review_assets+'</body></html>')

def read_and_render(ns, reg, account_review=False):
    # Legal reconciliation and HTML construction can take longer than the
    # indexed SQLite read. Keep both outside the shared HTTP event loop.
    return render(reg, read(ns, reg), account_review=account_review,
                  legal_review=read_legal_state(ns,reg))


class FastCardASGI:
    def __init__(self,app,ns):
        self.app=app
        self.ns=ns
    async def __call__(self,scope,receive,send):
        match=CARD_RE.fullmatch(scope.get('path','')) if scope.get('type')=='http' and scope.get('method')=='GET' else None
        if not match:
            return await self.app(scope,receive,send)
        reg=match.group(1)
        try:
            body=await asyncio.wait_for(asyncio.to_thread(
                read_and_render,self.ns,reg,bool(scope.get("eis223_account_id"))),timeout=6)
            result=HTMLResponse(body,headers={"Cache-Control":"no-store","X-Audit-Card":"saved-evidence-v343"})
        except (sqlite3.Error,TimeoutError,ValueError) as e:
            result=HTMLResponse(
             '<!doctype html><html lang="ru"><meta charset="utf-8"><title>Карточка проверки</title>'
             '<main style="font:17px system-ui;max-width:750px;margin:40px auto;padding:20px">'
             '<h1>Карточка '+esc(reg)+'</h1><p>База проверок занята. '
             'Сохранённые результаты не удалены. Повторите открытие через несколько секунд.</p>'
             '<a href="/check-new/'+esc(reg)+'">Повторить</a> · <a href="/">На главную</a></main>',
             status_code=503,headers={"Cache-Control":"no-store"})
        await result(scope,receive,send)

def install(ns):
    ns['app'].add_middleware(FastCardASGI,ns=ns)
    print('V343 FAST_READONLY_CARDS_READY saved_findings=True no_reanalysis=True',flush=True)
