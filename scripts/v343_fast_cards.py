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
STYLE="""<style>
:root{font-family:system-ui,-apple-system,Arial,sans-serif;color:#173b5d;background:#eff4f9}
*{box-sizing:border-box}body{margin:0}header{background:#184772;color:#fff;padding:20px max(16px,4vw)}
header h1{margin:0;font-size:21px}main{max-width:1180px;margin:20px auto;padding:0 15px 40px}
nav{display:flex;flex-wrap:wrap;gap:12px;margin-bottom:17px}
a{color:#17609d;text-decoration:none}a:hover{text-decoration:underline}
nav a,.action{display:inline-flex;align-items:center;border-radius:8px;padding:11px 13px;background:#fff;border:1px solid #c9d9e8;color:#17456c;font-weight:650}
.action{background:#225e98;color:white}
.panel{background:white;border:1px solid #d9e5ef;border-radius:12px;padding:18px;margin:14px 0}
.panel h2{font-size:19px;margin:0 0 14px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:9px 20px}
.legal{border-left:4px solid #b93838;background:#fffafa;padding:13px 15px;margin:10px 0;border-radius:6px}
.review{border-left:4px solid #b38a38;background:#fffdf7;padding:11px 14px;margin:9px 0;border-radius:6px}
.legal h3,.review h3{font-size:16px;margin:0 0 8px}.evidence{white-space:pre-wrap;overflow-wrap:anywhere}
.meta{font-size:14px;color:#536a7f}.chip{font-size:13px;padding:3px 9px;background:#e4effa;border-radius:15px}
table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:10px;border-bottom:1px solid #edf0f3;overflow-wrap:anywhere;vertical-align:top}
h2{color:#17466f}@media(max-width:640px){main{padding:0 10px 28px}header h1{font-size:18px}.panel{padding:13px}table,tbody,tr,td{display:block}thead{display:none}}
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

def detail_block(item, kind, title, law, evidence, document, source, link):
    return ('<article class="'+kind+'"><h3>'+esc(title or "Нарушение")+'</h3>'
      +('<p><b>Правовое основание:</b> '+esc(law)+'</p>' if law else '')
      +('<p class="evidence"><b>Обстоятельства и доказательства:</b> '+esc(evidence)+'</p>' if evidence else '')
      +('<p class="meta">Документ: '+esc(document)+'</p>' if document else '')
      +('<p>'+source_link(source,"Оригинал ЕИС")+'</p>' if source_link(source,"Оригинал ЕИС") else '')
      +('<p>'+source_link(link,"Норма права")+'</p>' if source_link(link,"Норма права") else '')
      +'</article>')

def render_thematic_panel(reg,thematic):
    """Reuse the same panels and finding cards used by the main audit view."""
    if not thematic:return ""
    batch=thematic["batch"]
    from v333_dedup_ui import collapse
    rows=collapse(thematic.get("findings",[]))
    labels=thematic.get("category_labels") or {}
    selected=thematic.get("categories") or []
    state=thematic.get("state") or "queued"
    names={"queued":"В очереди","completed":"Проверено",
      "waiting_originals":"Ожидаются оригиналы ЕИС",
      "waiting_engine":"Ожидается обработчик","retry":"Повторная попытка",
      "excluded_company":"Форма заказчика не соответствует выборке"}
    result=['<section class="panel" id="thematic-results">',
      '<h2>Нарушения тематической проверки: '+str(len(rows))+'</h2>',
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
    # Both independent tables may describe the SAME documented legal event.
    # Reconcile by title, law, document and substantive proof, not by the
    # source row's code or a different start of the quoted excerpt.
    candidates=[dict(f) for f in findings]
    candidates.extend({**c, "title":c.get("item") or "Нарушение"}
                      for c in checks if c.get("status")=="violation")
    unique=collapse(candidates,field="title")
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
            '">← К тематической очереди</a><a href="/check-new/'+esc(reg)+
            '">Общая карточка проверки</a><a href="/">Все закупки</a></nav>')
    thematic_html=render_thematic_panel(reg,thematic)
    actions='<p><a class="action" href="/api/v304/memo/'+esc(reg)+'.odt">Сформировать служебную записку ODT</a></p>' if violation else '<p class="meta">Служебная записка формируется при наличии подтверждённых нарушений.</p>'
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
    return ('<!doctype html><html lang="ru"><head><meta charset="utf-8">'
     '<meta name="viewport" content="width=device-width,initial-scale=1">'
     '<title>'+('Тематическая проверка ' if thematic else 'Карточка проверки ')+esc(reg)+'</title>'+STYLE+'</head><body>'
     '<header><h1>'+('Карточка тематической проверки № ' if thematic else 'Карточка проверки закупки № ')+esc(reg)+'</h1></header><main>'+nav
     +'<section class="panel"><h2>Сведения о закупке</h2>'+basic+'</section>'
     +thematic_html
     +'<section class="panel"><h2>'+esc(status)+'</h2>'+actions+notes+
        (''.join(violation) if violation else '<p>Подтверждённых нарушений в сохранённых результатах пока нет. Это не означает, что нарушений не существует.</p>')+'</section>'
     +'<section class="panel"><h2>Требует проверки</h2>'+checked+'</section>'
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
