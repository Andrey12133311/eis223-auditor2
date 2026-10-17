"""Safe private ZIP case export, local original search and document revisions.

Original documents are included only if they exist inside the trusted EIS data
volume and fit conservative size limits. Missing/unreadable files are listed as
missing, never silently substituted. No deletion or changes to originals.
"""
from __future__ import annotations
import difflib
from hashlib import sha256
import io
import json
from pathlib import Path
import re
import sqlite3
import zipfile
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from scripts.account_api_v1 import connect,current_account
from scripts.account_session_policy_v1 import no_store_headers
from scripts.v370_staff_workbench import case_owner,eis
from scripts.v369_evidence_case import _case_snapshot

router=APIRouter(prefix="/me/workbench",tags=["employee-original-documents"])
REG=re.compile(r"^3[0-9]{10}$")
MAX_TOTAL=35*1024*1024
MAX_EACH=8*1024*1024

def _document_rows(request,reg):
    con=eis(request)["conn"]()
    try:
        con.row_factory=sqlite3.Row
        con.execute("PRAGMA busy_timeout=1500")
        con.execute("PRAGMA query_only=ON")
        rows=con.execute(
            """SELECT id,name,doc_type,url,status,local_path,published_at,
                      metadata_json,text_chars FROM docs WHERE reg_number=?
               ORDER BY id DESC LIMIT 140""",(reg,)).fetchall()
        return [dict(r) for r in rows]
    finally:con.close()

def _safe_source(reg,entry,max_each=MAX_EACH):
    from v295_eis_originals import root
    raw=entry.get("local_path")
    if not raw:return None
    base=root().resolve()
    try:
        file=Path(str(raw)).resolve(strict=True)
        if not file.is_relative_to(base) or not file.is_file():
            return None
        size=file.stat().st_size
        if not (0<size<=max_each):return None
        metadata=json.loads(entry.get("metadata_json") or "{}")
        if not isinstance(metadata,dict):return None
        # Hash verification on saved artifacts protects against stale links.
        digest=sha256(file.read_bytes()).hexdigest()
        declared=metadata.get("sha256")
        if declared and digest!=declared:return None
        return file,digest
    except (OSError,ValueError,TypeError,json.JSONDecodeError):
        return None

def _original_text(request,file):
    from v314_eis_metadata import document_text
    ns=eis(request)
    extractor=ns.get("extract_text")
    if not callable(extractor):
        raise HTTPException(503,"Серверный экстрактор документов недоступен")
    try:
        result=document_text(file,extractor)
    except (ValueError,RuntimeError,OSError,TypeError) as exc:
        raise HTTPException(422,"Текст оригинала не удалось извлечь") from exc
    return str(result or "")[:180000]

@router.get("/{reg}/versions")
def available_versions(request:Request,reg:str,response:Response):
    case_owner(request,reg)
    response.headers.update(no_store_headers())
    rows=_document_rows(request,reg)
    names={}
    for row in rows:
        resolved=_safe_source(reg,row,max_each=5*1024*1024)
        if not resolved:continue
        name=re.sub(r"(?<!\d)(?:v|версия|редакция)[\s_-]*\d+","",
                    str(row.get("name") or ""),flags=re.I).strip()
        names.setdefault(name,[]).append({
            "document_id":row["id"],"name":row["name"],
            "published_at":row.get("published_at"),
            "sha256":resolved[1],
        })
    groups=[{"label":key,"versions":group} for key,group in names.items()
            if len({v["sha256"] for v in group})>1]
    return {"groups":groups[:35],
            "note":"Рядом показаны разные сохранённые версии только по совпадающему имени; редакции требуют визуального подтверждения."}

@router.get("/{reg}/compare")
def compare(request:Request,reg:str,left:int,right:int,response:Response):
    case_owner(request,reg)
    response.headers.update(no_store_headers())
    if left==right or min(left,right)<1:
        raise HTTPException(422,"Выберите два разных сохранённых документа")
    rows={row["id"]:row for row in _document_rows(request,reg)}
    if left not in rows or right not in rows:
        raise HTTPException(404,"Документ не относится к вашей закупке")
    l=_safe_source(reg,rows[left],max_each=5*1024*1024)
    r=_safe_source(reg,rows[right],max_each=5*1024*1024)
    if not l or not r:
        raise HTTPException(409,"Оригиналы этих редакций пока недоступны")
    one=_original_text(request,l[0]).splitlines()[:2000]
    two=_original_text(request,r[0]).splitlines()[:2000]
    differ=list(difflib.unified_diff(one,two,
        fromfile=str(rows[left]["name"] or "Редакция 1"),
        tofile=str(rows[right]["name"] or "Редакция 2"),
        lineterm="",n=2))
    return {"left_id":left,"right_id":right,
            "diff":differ[:800],
            "truncated":len(differ)>800 or len(one)>=2000 or len(two)>=2000,
            "note":"Отличия в оригинальном извлечённом тексте; это не автоматически признанные нарушения."}

@router.get("/{reg}/search-originals")
def search_originals(request:Request,reg:str,q:str,response:Response):
    case_owner(request,reg)
    response.headers.update(no_store_headers())
    query=q.strip()
    if len(query)<3 or len(query)>100:
        raise HTTPException(422,"Введите поисковую фразу от 3 до 100 символов")
    results=[]
    attempted=0
    for row in _document_rows(request,reg)[:40]:
        if attempted>=12:break
        resolved=_safe_source(reg,row,max_each=3*1024*1024)
        if not resolved:continue
        attempted+=1
        try:
            body=_original_text(request,resolved[0])
        except HTTPException:
            continue
        lower=body.casefold()
        start=0
        for _ in range(4):
            at=lower.find(query.casefold(),start)
            if at<0:break
            results.append({"document_id":row["id"],
                            "name":row["name"],
                            "source_url":row.get("url") or "",
                            "fragment":body[max(0,at-130):min(len(body),at+len(query)+210)]})
            start=at+len(query)
    response.headers.update(no_store_headers())
    return {"documents_scanned":attempted,"results":results[:35],
            "scope":"До 12 уже сохранённых оригиналов размером до 3 МБ. Сканированные изображения без извлечённого текста не входят."}

@router.get("/{reg}/export.zip")
def case_export(request:Request,reg:str):
    owner=case_owner(request,reg)
    report=_case_snapshot(eis(request),reg)
    docs=_document_rows(request,reg)
    out=io.BytesIO()
    manifest={"reg_number":reg,"documents":[],"limits":"Исходные документы до 8 МБ каждый, весь архив до 35 МБ",
              "violations":report["findings_count"],"missing":[]}
    used=0
    with zipfile.ZipFile(out,"w",compression=zipfile.ZIP_DEFLATED,compresslevel=4) as archive:
        archive.writestr("case/report.json",json.dumps(report,ensure_ascii=False,indent=2,default=str))
        for index,row in enumerate(docs):
            doc={"name":row["name"],"source_url":row.get("url"),
                 "published_at":row.get("published_at"),"status":row.get("status")}
            result=_safe_source(reg,row)
            if result is None:
                doc["archived"]=False;manifest["missing"].append(row["name"])
            else:
                file,digest=result
                size=file.stat().st_size
                if used+size>MAX_TOTAL:
                    doc["archived"]=False;manifest["missing"].append(row["name"])
                else:
                    # Never use untrusted paths from EIS in archive members.
                    member=f"originals/{index+1:03d}_{digest[:16]}{file.suffix.lower()}"
                    archive.write(file,member)
                    doc.update({"archived":True,"member":member,"sha256":digest})
                    used+=size
            manifest["documents"].append(doc)
        with connect() as db:
            memo=db.execute(
                """SELECT b.odt,m.id FROM account_memos m
                   JOIN account_memo_blobs b ON b.memo_id=m.id
                   WHERE m.user_id=%s AND m.reg_number=%s
                   ORDER BY m.created_at DESC LIMIT 1""",
                (owner,reg)).fetchone()
        if memo and memo[0] and len(memo[0])<=3*1024*1024:
            archive.writestr("reports/last_memo.odt",bytes(memo[0]))
            manifest["memo_archived"]=True
        else:manifest["memo_archived"]=False
        archive.writestr("case/manifest.json",json.dumps(manifest,ensure_ascii=False,indent=2))
        archive.writestr("README.txt",(
            "ЕИС Аудитор — комплект материалов проверки\n"
            "Содержимое: выводы программы, источники ЕИС, доступные оригиналы, "
            "опись и служебная записка (если есть).\n"
            "Отсутствующие или слишком большие файлы перечислены в manifest.json.\n"
            "Программные выводы и ручные предложения не заменяют юридическую оценку сотрудника.\n"
        ))
    if out.tell()>MAX_TOTAL+5*1024*1024:
        raise HTTPException(413,"Комплект превышает установленное ограничение")
    return Response(out.getvalue(),media_type="application/zip",
        headers={"Cache-Control":"private,no-store",
                 "X-Content-Type-Options":"nosniff",
                 "Content-Disposition":f'attachment; filename="eis_case_{reg}.zip"'})
