"""Truthful presentation of persisted procurement processing stages.

This module reads no storage and changes no audit decisions. A complete legal
record does not establish receipt/text extraction for every expected file.
The personal-list API has no per-file states, so its middle stages stay unknown
unless an active job explicitly establishes that retrieval is in progress.
"""
from __future__ import annotations

from html import escape
import re


_LABELS = ("Запрос принят", "Документы получены", "Текст извлечён", "Юридическая проверка")
_KEYS = ("request", "documents", "text", "legal")
_STATES = {"complete": "Готово", "current": "Выполняется", "waiting": "Ожидание",
           "unknown": "Уточняется", "error": "Требует внимания"}
_ACTIVE = {"pending", "queued", "running", "processing"}
_JOBS = {"queued", "leased", "done", "completed", "partial", "deferred", "error", "failed"}
_RECEIVED = {"checked", "downloaded_no_text"}


def _count(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, float):
        if not value.is_integer():
            return None
        value = int(value)
    raw = str(value).strip()
    if not re.fullmatch(r"[0-9]+", raw) or len(raw) > 16:
        return None
    result = int(raw)
    return result if result <= 9007199254740991 else None


def _state(value):
    return value.strip() if isinstance(value, str) else ""


def inspection_progress(*, purchase=None, documents=None, document_total=None,
                        legal_review=None, request_accepted=None, job_status=None,
                        has_previous=False, thematic=None):
    """Return four independent stages using only explicit persisted evidence.

    ``purchase`` uses documents_found, ``documents`` contains native docs.status
    and optional text_chars, and ``document_total`` is the total saved-row count
    (the card may pass only its latest 80 rows). Full middle-stage completion
    requires a complete, nonempty row snapshot matching documents_found. Stored
    files during a known new/failed run are described as previous observations.
    ``legal_review`` uses state/documents_read; thematic state overrides it.
    """
    purchase = purchase if isinstance(purchase, dict) else {}
    audit = legal_review if isinstance(legal_review, dict) else {}
    theme = thematic if isinstance(thematic, dict) else None
    legal = _state(theme.get("state", "unknown") if theme is not None else audit.get("state", "unknown"))
    job_status = _state(job_status)
    read = _count(audit.get("documents_read"))
    found = _count(purchase.get("documents_found"))
    total = _count(document_total)
    rows = documents if isinstance(documents, (list, tuple)) else None
    rows = [row if isinstance(row, dict) else {} for row in rows] if rows is not None else None
    statuses = [_state(row.get("status")) for row in rows or []]
    records = (purchase, audit, theme or {})
    coverage_incomplete = job_status == "partial" or any(record.get("source_incomplete") is True for record in records)
    for record in records:
        expected, received_originals = _count(record.get("original_expected")), _count(record.get("original_received"))
        if expected is not None and received_originals is not None and expected != received_originals:
            coverage_incomplete = True
    unreadable = any((_count(record.get("documents_unreadable")) or 0) > 0 for record in records)
    accepted = request_accepted is True or (request_accepted is not False and
               (job_status in _JOBS or legal in _ACTIVE | {"completed", "failed", "error", "no_readable_documents"}
                or (theme is not None and legal in {"waiting_originals", "waiting_engine", "retry"})))
    stale = bool(has_previous and (legal in _ACTIVE | {"failed", "error"}
                 or job_status in {"queued", "leased", "deferred"}
                 or (theme is not None and legal in {"waiting_originals", "waiting_engine", "retry"})))
    complete_set = bool(found and total == found and rows is not None and len(rows) == total)
    stage = lambda index, state, detail: {"key": _KEYS[index], "label": _LABELS[index],
                                          "state": state, "status": _STATES[state], "detail": detail}
    result = [stage(0, "complete" if accepted else "unknown",
                    "Запрос сохранён." if accepted else "Подтверждение запроса пока не получено.")]

    # Counts of checked documents or saved rows are never download counts.
    received = sum(value in _RECEIVED for value in statuses)
    if stale:
        file_state, file_note = "unknown", "Показаны сохранённые сведения о файлах; новый запуск ещё не завершён."
    elif any(value == "error" for value in statuses):
        file_state, file_note = "error", "Есть ошибка получения или обработки документа."
    elif job_status in {"error", "failed"}:
        file_state, file_note = "error", "Получение данных завершилось с ошибкой."
    elif coverage_incomplete:
        file_state, file_note = "unknown", "Полнота набора исходных документов не подтверждена."
    elif complete_set and all(value in _RECEIVED for value in statuses):
        file_state, file_note = "complete", "Получены известные файлы: " + str(received) + " из " + str(found) + "."
    elif job_status == "leased":
        file_state, file_note = "current", "Идёт получение данных ЕИС."
    elif any(value in {"metadata", "waiting_for_originals"} for value in statuses) or job_status in {"queued", "deferred"}:
        file_state, file_note = "waiting", "Ожидается получение файлов ЕИС."
    elif found == 0 and total == 0:
        file_state, file_note = "waiting", "Полученные файлы пока не подтверждены."
    else:
        file_state, file_note = "unknown", "Полнота получения файлов пока не подтверждена."
    result.append(stage(1, file_state, file_note))

    contradictory = any(_state(row.get("status")) == "checked" and row.get("text_chars") is not None
                        and (_count(row.get("text_chars")) or 0) < 3 for row in rows or [])
    if stale:
        text_state, text_note = "unknown", "Извлечение текста для нового запуска ещё не подтверждено."
    elif any(value in {"downloaded_no_text", "archive_unreadable"} for value in statuses) or legal == "no_readable_documents" or unreadable:
        text_state, text_note = "error", "Не удалось извлечь пригодный текст из всех доступных документов."
    elif coverage_incomplete:
        text_state, text_note = "unknown", "Полнота извлечения текста пока не подтверждена."
    elif contradictory:
        text_state, text_note = "unknown", "Состояние сохранённого текста требует уточнения."
    elif complete_set and all(value == "checked" for value in statuses) and file_state != "error":
        text_state, text_note = "complete", "Текст получен для известных документов: " + str(found) + "."
    elif file_state in {"current", "waiting"}:
        text_state, text_note = "waiting", "Ожидается пригодный текст документов."
    else:
        text_state, text_note = "unknown", "Полнота извлечения текста пока не подтверждена."
    result.append(stage(2, text_state, text_note))

    if legal in {"running", "processing"}:
        legal_state, legal_note = "current", "Выполняется юридическая проверка."
    elif legal in {"pending", "queued", "waiting_originals", "waiting_engine", "retry"}:
        legal_state, legal_note = "waiting", "Проверка ожидает обработки или необходимых документов."
    elif legal in {"failed", "error"}:
        legal_state, legal_note = "error", "Юридическая проверка завершилась с ошибкой."
    elif legal == "no_readable_documents":
        legal_state, legal_note = "error", "Проверка не выполнена: нет пригодного текста документов."
    elif legal == "completed" and (theme is not None or read is not None and read > 0):
        if stale:
            legal_state, legal_note = "unknown", "Сохранённая проверка завершена; состояние нового запуска уточняется."
        else:
            legal_state, legal_note = "complete", "Завершена по сохранённой записи. Полнота источников оценивается отдельно."
    elif legal == "completed" and read == 0:
        legal_state, legal_note = "error", "Завершение не подтверждено: нет прочитанных документов."
    elif theme is not None and legal == "excluded_company":
        legal_state, legal_note = "unknown", "Закупка исключена из тематической выборки."
    else:
        legal_state, legal_note = "unknown", "Юридическая проверка ещё не подтверждена."
    result.append(stage(3, legal_state, legal_note))
    return result


STYLE = r'''<style class="inspection-progress-style">
.inspection-progress{margin:18px 0 0;padding-top:16px;border-top:1px solid #e5e5ea;color:#1d1d1f}
.inspection-progress h3{font-size:12px;font-weight:600;letter-spacing:0;margin:0 0 12px}
.inspection-progress ol{list-style:none;display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:0;padding:0}
.inspection-progress li{min-width:0;position:relative;padding:0 0 0 28px;font-size:12px;line-height:1.5}
.inspection-progress .inspection-step-marker{position:absolute;left:0;top:1px;display:grid;place-items:center;width:20px;height:20px;border:1px solid #d6d6dc;border-radius:50%;font-size:10px;color:#636366}
.inspection-progress .inspection-step-title{display:block;font-size:12px;font-weight:600;color:#1d1d1f}
.inspection-progress .inspection-step-status{display:block;font-size:11px;color:#636366;margin:3px 0}
.inspection-progress .inspection-step-detail{display:block;color:#636366;font-size:11px;overflow-wrap:anywhere}
.inspection-progress .inspection-step-complete .inspection-step-marker{border-color:#b8cfe9;background:#eaf2ff;color:#0055ad}
.inspection-progress .inspection-step-current .inspection-step-marker{background:#0066cc;border-color:#0066cc;color:white}
.inspection-progress .inspection-step-error .inspection-step-marker{background:#fff2f3;border-color:#edc3c8;color:#b42332}
.inspection-progress .inspection-step-unknown .inspection-step-marker{border-style:dashed}
.inspection-progress.inspection-progress-compact{padding-top:10px;margin-top:10px}
.inspection-progress-compact>summary{cursor:pointer;min-height:32px;display:list-item;padding:6px 0;font-size:12px;font-weight:600;color:#0055ad}
.inspection-progress-compact[open]>summary{margin-bottom:10px}
.inspection-progress-compact>summary:focus-visible{outline:3px solid #0066cc;outline-offset:3px;border-radius:4px}
.inspection-progress-compact ol{grid-template-columns:minmax(0,1fr);gap:8px}
.inspection-progress-compact li{padding-left:25px}.inspection-progress-compact .inspection-step-title{display:inline}
.inspection-progress-compact .inspection-step-status{display:inline;margin-left:6px}.inspection-progress-compact .inspection-step-detail{margin-top:1px}
.eis-quicklook .inspection-progress ol{grid-template-columns:minmax(0,1fr);gap:12px}
@media(max-width:720px){.inspection-progress ol{grid-template-columns:repeat(2,minmax(0,1fr));gap:16px 12px}}
@media(max-width:430px){.inspection-progress ol{grid-template-columns:minmax(0,1fr);gap:12px}}
@media(pointer:coarse){.inspection-progress-compact>summary{min-height:44px;box-sizing:border-box;padding:12px 0}}
@media(prefers-reduced-motion:reduce){.inspection-progress *{animation:none!important;transition:none!important}}
</style>'''


def render_inspection_progress(*, compact=False, **kwargs):
    """Render an accessible, non-interactive ordered list without percentages."""
    steps = inspection_progress(**kwargs)
    result = [STYLE, '<section class="inspection-progress' + (' inspection-progress-compact' if compact else '') +
              '" aria-label="Ход проверки"><h3>Ход проверки</h3><ol>']
    for index, item in enumerate(steps, 1):
        marker = "✓" if item["state"] == "complete" else "!" if item["state"] == "error" else str(index)
        current = ' aria-current="step"' if item["state"] == "current" else ''
        result.append('<li class="inspection-step-' + item["state"] + '"' + current + '>' +
                      '<span class="inspection-step-marker" aria-hidden="true">' + marker + '</span>' +
                      '<span class="inspection-step-title">' + escape(item["label"]) + '</span>' +
                      '<span class="inspection-step-status">' + escape(item["status"]) + '</span>' +
                      '<span class="inspection-step-detail">' + escape(item["detail"]) + '</span></li>')
    return ''.join(result) + '</ol></section>'


def manual_progress(item):
    """Adapt the existing /me/manual-checks item without inventing file states."""
    item = item if isinstance(item, dict) else {}
    return inspection_progress(
        purchase={"documents_found": item.get("documents_found")},
        legal_review={"state": item.get("legal_review_state"),
                      "documents_read": item.get("legal_review_documents")},
        request_accepted=bool(re.fullmatch(r"3[0-9]{10}", str(item.get("reg_number") or ""))),
        job_status=item.get("job_status"),
        has_previous=bool((_count(item.get("documents_checked")) or 0) > 0 or
                          (_count(item.get("violations")) or 0) > 0))


MANUAL_SCRIPT = r'''<script class="inspection-progress-runtime">
(()=>{
'use strict';
const labels=['Запрос принят','Документы получены','Текст извлечён','Юридическая проверка'];
const keys=['request','documents','text','legal'];
const names={complete:'Готово',current:'Выполняется',waiting:'Ожидание',unknown:'Уточняется',error:'Требует внимания'};
const count=value=>{
 if(typeof value==='string'){const raw=value.trim();if(!/^[0-9]+$/.test(raw)||raw.length>16)return null;value=Number(raw)}
 return typeof value==='number'&&Number.isSafeInteger(value)&&value>=0?value:null;
};
const state=value=>typeof value==='string'?value.trim():'';
function model(item){
 item=item&&typeof item==='object'&&!Array.isArray(item)?item:{};
 const legal=state(item.legal_review_state),job=state(item.job_status),read=count(item.legal_review_documents);
 const previous=(count(item.documents_checked)||0)>0||(count(item.violations)||0)>0;
 const stale=previous&&(['pending','queued','running','processing','failed','error'].includes(legal)||['queued','leased','deferred'].includes(job));
 const stage=(index,status,detail)=>({key:keys[index],label:labels[index],state:status,status:names[status],detail});
 const accepted=/^3[0-9]{10}$/.test(String(item.reg_number||''));
 const result=[stage(0,accepted?'complete':'unknown',accepted?'Запрос сохранён.':'Подтверждение запроса пока не получено.')];
 let fileStatus='unknown',fileNote='Полнота получения файлов пока не подтверждена.';
 if(stale){fileNote='Показаны сохранённые сведения о файлах; новый запуск ещё не завершён.'}
 else if(job==='leased'){fileStatus='current';fileNote='Идёт получение данных ЕИС.'}
 else if(['error','failed'].includes(job)){fileStatus='error';fileNote='Получение данных завершилось с ошибкой.'}
 else if(job==='partial'){fileNote='Полнота набора исходных документов не подтверждена.'}
 else if(['queued','deferred'].includes(job)){fileStatus='waiting';fileNote='Ожидается получение файлов ЕИС.'}
 result.push(stage(1,fileStatus,fileNote));
 let textStatus='unknown',textNote='Полнота извлечения текста пока не подтверждена.';
 if(stale){textNote='Извлечение текста для нового запуска ещё не подтверждено.'}
 else if(legal==='no_readable_documents'){textStatus='error';textNote='Не удалось извлечь пригодный текст из всех доступных документов.'}
 else if(['current','waiting'].includes(fileStatus)){textStatus='waiting';textNote='Ожидается пригодный текст документов.'}
 result.push(stage(2,textStatus,textNote));
 let legalStatus='unknown',legalNote='Юридическая проверка ещё не подтверждена.';
 if(['running','processing'].includes(legal)){legalStatus='current';legalNote='Выполняется юридическая проверка.'}
 else if(['pending','queued','waiting_originals','waiting_engine','retry'].includes(legal)){legalStatus='waiting';legalNote='Проверка ожидает обработки или необходимых документов.'}
 else if(['failed','error'].includes(legal)){legalStatus='error';legalNote='Юридическая проверка завершилась с ошибкой.'}
 else if(legal==='no_readable_documents'){legalStatus='error';legalNote='Проверка не выполнена: нет пригодного текста документов.'}
 else if(legal==='completed'&&read!==null&&read>0){
  if(stale){legalNote='Сохранённая проверка завершена; состояние нового запуска уточняется.'}
  else{legalStatus='complete';legalNote='Завершена по сохранённой записи. Полнота источников оценивается отдельно.'}
 }else if(legal==='completed'&&read===0){legalStatus='error';legalNote='Завершение не подтверждено: нет прочитанных документов.'}
 result.push(stage(3,legalStatus,legalNote));return result;
}
function create(item){
 const wrapper=document.createElement('details');wrapper.className='inspection-progress inspection-progress-compact';
 const summary=document.createElement('summary');summary.textContent='Ход проверки';wrapper.append(summary);
 const list=document.createElement('ol');list.setAttribute('aria-label','Этапы проверки');
 model(item).forEach((step,index)=>{
  const row=document.createElement('li');row.className='inspection-step-'+step.state;
  if(step.state==='current')row.setAttribute('aria-current','step');
  const marker=document.createElement('span');marker.className='inspection-step-marker';marker.setAttribute('aria-hidden','true');
  marker.textContent=step.state==='complete'?'✓':step.state==='error'?'!':String(index+1);row.append(marker);
  for(const [className,key] of [['title','label'],['status','status'],['detail','detail']]){
   const text=document.createElement('span');text.className='inspection-step-'+className;text.textContent=step[key];row.append(text);
  }
  list.append(row);
 });wrapper.append(list);return wrapper;
}
window.eis223InspectionProgress=Object.freeze({model,create});
})();
</script>'''


def manual_progress_runtime():
    """Include once before the account-list script; create(item) returns details."""
    return STYLE + MANUAL_SCRIPT
