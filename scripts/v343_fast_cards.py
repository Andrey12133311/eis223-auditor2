"""Fast, read-only procurement card from durable SQLite evidence.
Installed as the outermost ASGI layer to avoid legacy card re-analysis and
the long chain of card HTML middleware on the critical GET request.
"""
from __future__ import annotations
import asyncio
import html
import re
import sqlite3
from urllib.parse import urlsplit
from fastapi.responses import HTMLResponse

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
 .grid{grid-template-columns:minmax(0,1fr)}
 table{display:block;overflow-x:auto;-webkit-overflow-scrolling:touch}
 th,td{padding:10px}
}
</style>"""

def esc(x):
    return html.escape(str(x if x is not None else ""), quote=True)

def source_link(url, title):
    try:
        p=urlsplit(str(url or ""))
        if p.scheme not in ("https", "http") or not p.hostname:
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

def read(ns,reg):
    c=ns["conn"]()
    try:
        c.row_factory=sqlite3.Row
        c.execute("PRAGMA busy_timeout=1200")
        c.execute("PRAGMA query_only=ON")
        p=c.execute("SELECT * FROM purchases WHERE reg_number=?",(reg,)).fetchone()
        purchase=dict(p) if p else None
        findings=[dict(r) for r in c.execute(
          """SELECT code,title,kind,law,law_url,evidence,document,source_url
             FROM findings WHERE reg_number=? AND kind='violation'
             ORDER BY id DESC LIMIT 350""",(reg,))]
        checks=[dict(r) for r in c.execute(
          """SELECT item,status,law,law_url,evidence,document,source_url
             FROM checks WHERE reg_number=? AND status IN ('violation','check')
             ORDER BY id DESC LIMIT 350""",(reg,))]
        docs=[dict(r) for r in c.execute(
          """SELECT name,doc_type,status,url,text_chars FROM docs WHERE reg_number=?
             ORDER BY id DESC LIMIT 80""",(reg,))]
        ftotal=c.execute("SELECT COUNT(*) FROM findings WHERE reg_number=? AND kind='violation'",(reg,)).fetchone()[0]
        ctotal=c.execute("SELECT COUNT(*) FROM checks WHERE reg_number=? AND status='violation'",(reg,)).fetchone()[0]
        dtotal=c.execute("SELECT COUNT(*) FROM docs WHERE reg_number=?",(reg,)).fetchone()[0]
        return purchase,findings,checks,docs,ftotal,ctotal,dtotal
    finally:
        c.close()

def legal_links(item, law=None, link=None):
    """Build only verified ConsultantPlus law links; never use EIS URL filter."""
    from v365_consultant_refs import references, consultant_url
    record={**(item or {}),"law":law or (item or {}).get("law"),
            "law_url":link or (item or {}).get("law_url")}
    refs=references(record)
    if refs:
        return '<p class="meta"><b>Точная норма:</b> '+'; '.join(
          '<a href="'+esc(ref["url"])+'" target="_blank" rel="noopener noreferrer">'+
          esc(ref["label"])+' · КонсультантПлюс ↗</a>' for ref in refs)+'</p>'
    if consultant_url(link):
        return ('<p class="meta"><b>Источник нормы:</b> <a href="'+esc(link)+
                '" target="_blank" rel="noopener noreferrer">'+
                'КонсультантПлюс · открыть норму ↗</a></p>')
    return '<p class="meta">Точная часть или пункт статьи требуют юридического уточнения.</p>'


def detail_block(item, kind, title, law, evidence, document, source, link):
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
    return ('<article class="'+kind+'"><h3>'+esc(title or "Нарушение")+'</h3>'
      +('<p><b>Правовое основание:</b> '+esc(law)+'</p>' if law else '')
      +('<p class="evidence"><b>Обстоятельства и доказательства:</b> '+esc(evidence)+'</p>' if evidence else '')
      +('<p class="meta">Документ: '+esc(document)+'</p>' if document else '')
      +('<p>'+source_link(source,"Оригинал ЕИС")+'</p>' if source_link(source,"Оригинал ЕИС") else '')
      +legal_links(item,law,link)
      +extra
      +'</article>')

def render_thematic_panel(reg,thematic):
    """Reuse the same panels and finding cards used by the main audit view."""
    if not thematic:return ""
    batch=thematic["batch"]
    from v352_dashboard_truth import collapse_legal_events
    rows=collapse_legal_events(thematic.get("findings",[]))
    labels=thematic.get("category_labels") or {}
    selected=thematic.get("categories") or []
    state=thematic.get("state") or "queued"
    names={"queued":"В очереди","completed":"Проверено",
      "waiting_originals":"Ожидаются оригиналы ЕИС",
      "waiting_engine":"Ожидается обработчик","retry":"Повторная попытка",
      "excluded_company":"Форма заказчика не соответствует выборке"}
    result=['<section class="panel" id="thematic-results">',
      '<h2>Нарушения: '+str(len(rows))+'</h2>',
      '<div class="grid"><div><b>Состояние проверки:</b> '+esc(names.get(state,state))+
      '</div><div><b>Выбранные направления:</b> '+
      esc(', '.join(labels.get(c,c) for c in selected) or "Не указаны")+
      '</div></div>']
    if thematic.get("detail"):
        result.append('<p class="meta">'+esc(thematic["detail"])+'</p>')
    if rows:
        result.append('<p><a class="action" href="/thematic/card/'+esc(batch)+'/'+
          esc(reg)+'?print=1" target="_blank" rel="noopener noreferrer">'+
          'Проект служебной записки · печать / PDF</a></p>')
        result.append('<p class="meta">Проект формируется по сохранённым тематическим нарушениям; '+
          'перед подписанием необходима юридическая сверка с оригиналами ЕИС.</p>')
    else:
        result.append('<p>Подтверждённых нарушений по выбранным направлениям пока нет. '+
          'Закупка может ещё ожидать проверки документов.</p>')
    for item in rows:
        category=labels.get(item.get("category"),item.get("category") or "Без категории")
        block=detail_block(item,"legal",item.get("title"),item.get("law"),
            item.get("evidence"),item.get("document"),
            item.get("source_url"),item.get("law_url"))
        result.append(block.replace('<article class="legal">',
              '<article class="legal"><p class="meta">Категория: '+
              esc(category)+'</p>',1))
    result.append('</section>')
    return ''.join(result)

def render(reg,data,thematic=None):
    p,findings,checks,docs,ftotal,ctotal,dtotal=data
    from v333_dedup_ui import collapse
    from v352_dashboard_truth import collapse_legal_events
    candidates=[dict(f) for f in findings]
    candidates.extend({**c, "title":c.get("item") or "Нарушение"}
                      for c in checks if c.get("status")=="violation")
    unique=collapse_legal_events(candidates)
    violation=[detail_block(item,"legal",item.get("title"),item.get("law"),
          item.get("evidence"),item.get("document"),item.get("source_url"),
          item.get("law_url")) for item in unique]
    reviews=collapse([dict(x) for x in checks if x.get("status")=="check"],field="item")
    # Display counts must agree with the number of distinct cards. Historical
    # findings and checks remain untouched and available in the ledger.
    status=(("Нарушения общей проверки: " if thematic else "Подтверждённые нарушения: ")+str(len(violation)))
    basic=''
    if p:
        keys=[
          ('Предмет закупки',val(p,'title')),('Заказчик',val(p,'customer')),
          ('ИНН заказчика',val(p,'customer_inn')),('Способ закупки',val(p,'method')),
          ('Стоимость закупки',val(p,'price')),('Дата размещения',val(p,'published_at')),
          ('Начало подачи заявок',val(p,'submission_start')),
          ('Окончание подачи заявок',val(p,'submission_end'))]
        if not val(p,'price') and val(p,'price_num') is not None:
            keys[4]=('Стоимость закупки',val(p,'price_num'))
        basic='<div class="grid">'+''.join('<div><b>'+esc(k)+':</b> '+text(v)+'</div>' for k,v in keys)+'</div>'
        basic+='<p class="meta">Документы: проверено '+str(int(val(p,'documents_checked') or 0))+' из '+str(int(val(p,'documents_found') or dtotal))+'</p>'
        eis=source_link(p.get('url'),'Открыть карточку ЕИС')
        if eis:basic+='<p>'+eis+'</p>'
    else:
        basic='<p>Карточка закупки пока не сохранена. После получения сведений ЕИС она появится здесь автоматически. Ранее найденные нарушения, если они есть в базе, показаны ниже.</p>'
    nav=('<nav><a href="/">Все закупки</a><a href="/manual-purchases">Самостоятельные проверки</a>'
         '<a href="/thematic">Тематические проверки</a></nav>')
    if thematic:
        nav=('<nav><a href="/thematic#'+esc(thematic["batch"])+
             '">← К тематической очереди</a></nav>')
    thematic_html=render_thematic_panel(reg,thematic)
    actions='<p><a class="action" href="/api/v304/memo/'+esc(reg)+'.odt">Сформировать служебную записку ODT</a></p>' if violation else '<p class="meta">Служебная записка формируется при наличии подтверждённых нарушений.</p>'
    if thematic:actions=''
    checked='<p class="meta">'+str(len(reviews))+' вопросов требуют дополнительной юридической проверки.</p>'
    for x in reviews[:50]:
        checked+=detail_block(x,'review',x.get('item'),x.get('law'),x.get('evidence'),x.get('document'),x.get('source_url'),x.get('law_url'))
    if len(reviews)>50:checked+='<p>Показаны первые 50 вопросов для проверки.</p>'
    docs_html='<table><thead><tr><th>Документ</th><th>Вид</th><th>Состояние</th><th>Источник</th></tr></thead><tbody>'
    for d in docs:
        docs_html+='<tr><td>'+text(d.get('name'))+'</td><td>'+text(d.get('doc_type'))+'</td><td>'+text(d.get('status'))+'</td><td>'+source_link(d.get('url'),'ЕИС')+'</td></tr>'
    docs_html+='</tbody></table>'
    if dtotal>len(docs):docs_html+='<p class="meta">Показаны последние '+str(len(docs))+' из '+str(dtotal)+' документов.</p>'
    if not docs:docs_html='<p>Оригиналы документов пока не сохранены.</p>'
    if ftotal+ctotal>len(violation):
        notes=('<p class="meta">Уникальных правовых эпизодов: '+str(len(violation))+
               '. Исходных подтверждённых записей: '+str(ftotal+ctotal)+
               '. Повторы объединены только в интерфейсе; доказательства сохранены в базе.</p>')
    else:notes=''
    # The thematic card is its own audit. Never append general findings or
    # general "review" cards underneath the thematic violations.
    general_section=('' if thematic else
        '<section class="panel"><h2>'+esc(status)+'</h2>'+actions+notes+
        (''.join(violation) if violation else
        '<p>Подтверждённых нарушений в сохранённых результатах пока нет. '
        'Это не означает, что нарушений не существует.</p>')+'</section>')
    review_section='' if thematic else '<section class="panel"><h2>Требует проверки</h2>'+checked+'</section>'
    return ('<!doctype html><html lang="ru"><head><meta charset="utf-8">'
     '<meta name="viewport" content="width=device-width,initial-scale=1">'
     '<title>'+('Тематическая проверка ' if thematic else 'Карточка проверки ')+esc(reg)+'</title>'+STYLE+'</head><body>'
     '<header><h1>'+('Карточка тематической проверки № ' if thematic else 'Карточка проверки закупки № ')+esc(reg)+'</h1></header><main>'+nav
     +'<section class="panel"><h2>Сведения о закупке</h2>'+basic+'</section>'
     +thematic_html
     +general_section+review_section
     +'<section class="panel"><h2>Документы ЕИС ('+str(dtotal)+')</h2>'+docs_html+'</section>'
     +'</main></body></html>')

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
            data=await asyncio.wait_for(asyncio.to_thread(read,self.ns,reg),timeout=6)
            result=HTMLResponse(render(reg,data),headers={"Cache-Control":"no-store","X-Audit-Card":"saved-evidence-v343"})
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
