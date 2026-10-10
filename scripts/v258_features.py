"""V258: accurate read-only progress reporting; no fabricated checked counts."""
from __future__ import annotations
import sqlite3

PROGRESS_JS = r'''<script id="v258-progress-ui">(()=>{'use strict';
if(location.pathname!=='/')return;
const root=document.querySelector('main')||document.body;
if(document.getElementById('v258-progress'))return;
const box=document.createElement('section');
box.id='v258-progress';
box.setAttribute('aria-label','Ход проверки документов');
box.style.cssText='margin:10px 0 16px;padding:10px 0;border-top:1px solid #cbd6d0;border-bottom:1px solid #cbd6d0;line-height:1.8';
box.textContent='Загружаем статистику обработки…';
root.insertBefore(box,root.firstChild);
const fmt=x=>new Intl.NumberFormat('ru-RU').format(Number(x)||0);
async function refresh(){
 try{const res=await fetch('/api/v258/progress',{cache:'no-store'});
 if(!res.ok)throw Error('HTTP '+res.status);
 const s=await res.json();
 box.textContent='Карточек в базе: '+fmt(s.card_records)+' · Закупок с проверенным оригиналом: '+fmt(s.verified_purchases)+' · Прочитано файлов: '+fmt(s.readable_documents)+' из '+fmt(s.document_records)+' · В очереди: '+fmt(s.queued_documents)+'. '+(s.deferred_documents?'Часть оригиналов ожидает доступности источника.':'');
 }catch(e){box.textContent='Статистика проверок временно недоступна.'}}
refresh();setInterval(()=>{if(!document.hidden)refresh()},30000);
})();</script>'''

def progress_html(body: str) -> str:
    if 'id="v258-progress-ui"' in body or '</body>' not in body:
        return body
    return body.replace('</body>', PROGRESS_JS+'</body>',1)

def install(ns: dict) -> None:
    app=ns['app']
    def snapshot():
        c=ns['conn']()
        try:
            c.row_factory=sqlite3.Row
            p=c.execute('SELECT COUNT(*) FROM purchases').fetchone()[0]
            d=c.execute("""SELECT COUNT(*) total,
              SUM(CASE WHEN status='checked' AND COALESCE(text_chars,0)>=3
                        AND COALESCE(local_path,'')<>'' THEN 1 ELSE 0 END) checked
              FROM docs""").fetchone()
            try:
                rows=c.execute("SELECT status,COUNT(*) n FROM document_jobs_v204 GROUP BY status").fetchall()
                queue={r['status']:r['n'] for r in rows}
            except sqlite3.OperationalError:queue={}
        finally:
            c.close()
        return {
            'card_records':p,
            'verified_purchases':len(ns['_verified_set235']()),
            'document_records':d['total'] or 0,
            'readable_documents':d['checked'] or 0,
            'queued_documents':sum(v for k,v in queue.items() if k!='checked'),
            'deferred_documents':queue.get('waiting',0),
            'source_waiting_not_checked':True,
        }

    @app.get('/api/v258/progress',include_in_schema=False)
    def get_progress():return snapshot()

    # Existing V257 HTML middleware calls this function by module global lookup.
    # Avoid adding another streaming middleware to the busy production app.
    prior=ns['_v257_features']._html_patch
    def updated(body):
        return progress_html(prior(body))
    ns['_v257_features']._html_patch=updated
    print('V258 PROGRESS_STATUS_READY',flush=True)
