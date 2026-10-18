"""Owner-scoped account favorites, in-app notifications and verified memo archive.

All EIS reads are non-mutating. Never claim a completed review based solely
on a finished EIS download job. Old memo metadata is preserved; unavailable
legacy files are not invented. No file names/paths supplied by the client.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
import io
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from uuid import UUID, uuid4
import zipfile

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ConfigDict, field_validator
from scripts.account_api_v1 import connect, current_account, require_origin
from scripts.account_session_policy_v1 import no_store_headers

router = APIRouter(prefix="/me", tags=["account-productivity"])
REG = re.compile(r"^3[0-9]{10}$")
INN = re.compile(r"^[0-9]{10}(?:[0-9]{2})?$")
MAX_MEMO = 3 * 1024 * 1024
MAX_PDF = 9 * 1024 * 1024


def valid_favorite(kind, key):
    return (kind == "procurement" and bool(REG.fullmatch(key)) or
            kind == "customer" and bool(INN.fullmatch(key)))


@router.get("/favorites")
def favorites(request: Request, response: Response):
    owner = current_account(request)[0]
    response.headers.update(no_store_headers())
    with connect() as db:
        rows = db.execute(
            """SELECT kind,entity_key,label,created_at FROM account_favorites
               WHERE user_id=%s ORDER BY created_at DESC LIMIT 250""",
            (owner,)).fetchall()
    return [{"kind":row[0],"key":row[1],"label":row[2],
             "created_at":row[3].isoformat()} for row in rows]


@router.post("/favorites/{kind}/{key}", status_code=201)
def favorite_add(kind: str, key: str, request: Request, label: str = ""):
    require_origin(request)
    owner = current_account(request)[0]
    if not valid_favorite(kind, key):
        raise HTTPException(422,"Для закупки нужен номер ЕИС, для заказчика — ИНН")
    clean_label = re.sub(r"[\x00-\x1f\x7f]", " ", label).strip()[:130]
    with connect() as db:
        db.execute(
            """INSERT INTO account_favorites(user_id,kind,entity_key,label)
               VALUES(%s,%s,%s,%s)
               ON CONFLICT(user_id,kind,entity_key) DO UPDATE SET label=
                 CASE WHEN EXCLUDED.label <> '' THEN EXCLUDED.label
                      ELSE account_favorites.label END""",
            (owner,kind,key,clean_label))
    return {"saved":True,"kind":kind,"key":key}


@router.delete("/favorites/{kind}/{key}")
def favorite_remove(kind: str, key: str, request: Request):
    require_origin(request)
    owner = current_account(request)[0]
    if not valid_favorite(kind, key):
        raise HTTPException(422,"Неверный номер или ИНН")
    with connect() as db:
        result = db.execute(
            "DELETE FROM account_favorites WHERE user_id=%s AND kind=%s AND entity_key=%s",
            (owner,kind,key))
    return {"removed":bool(result.rowcount)}


def _observations(request, owner):
    """Real verified count snapshots. No optimistic predictions or queued findings."""
    ns=getattr(request.app.state,"eis223_manual_ns",None)
    if ns is None:
        raise HTTPException(503,"Данные проверок временно недоступны")
    from scripts.v366_account_manual import _read_only_status, MANUAL_OWNED
    from v342_thematic_queue import thematic_history_records
    from v360_thematic_ownership import linked_ids
    with connect() as db:
        rows=db.execute(MANUAL_OWNED,(owner,)).fetchall()
    records=[{"reg_number":row[0],"job_id":row[1],
              "created_at":row[2],"last_started_at":row[3]} for row in rows]
    manual=_read_only_status(ns,records)
    allowed=linked_ids(request)
    thematic=thematic_history_records(ns, allowed)["batches"]
    snapshots=[]
    for item in manual:
        snapshots.append({
            "key":"manual:"+item["reg_number"],
            "status":("review_completed" if item.get("legal_review_completed")
                      else item["job_status"]),
            "violations":max(0,int(item["violations"])),
            "docs":max(0,int(item["documents_checked"])),
            "found":max(0,int(item["documents_found"])),
            "label":"Закупка № "+item["reg_number"],
            "url":"/check-new/"+item["reg_number"] if item["card_available"] else "/account/home",
        })
    for item in thematic:
        snapshots.append({
            "key":"thematic:"+item["id"],
            "status":str(item["status"]),
            "violations":max(0,int(item["violations"])),
            "docs":0,"found":0,
            "label":"Тематическая проверка",
            "url":"/account/thematic",
        })
    return snapshots


def _reconcile_alerts(db, owner, snapshots):
    """Baseline the first observation; notify only on true subsequent change.

    Transaction locks protect concurrent browser tabs from duplicate alerts.
    No global alerts and no alert for an unverified positive number.
    """
    for snapshot in snapshots:
        key=snapshot["key"]
        db.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (str(owner)+":"+key,))
        old=db.execute(
            """SELECT status,violation_count,documents_checked
               FROM account_alert_snapshots WHERE user_id=%s AND source_key=%s
               FOR UPDATE""", (owner,key)).fetchone()
        status=str(snapshot["status"])
        violations=snapshot["violations"]
        checked=snapshot["docs"]
        if old:
            old_status,old_count,old_checked=old
            if violations>old_count:
                delta=violations-old_count
                db.execute(
                    """INSERT INTO account_alert_events
                      (user_id,source_key,kind,title,detail) VALUES(%s,%s,%s,%s,%s)""",
                    (owner,key,"new_violation",
                     "Новые подтверждённые нарушения",
                     snapshot["label"]+": +"+str(delta)+"; всего "+str(violations)))
            if (key.startswith("manual:") and
                status=="review_completed" and old_status!="review_completed"): 
                db.execute(
                    """INSERT INTO account_alert_events
                      (user_id,source_key,kind,title,detail) VALUES(%s,%s,%s,%s,%s)""",
                    (owner,key,"review_completed",
                     "Юридическая проверка завершена",
                     snapshot["label"]+": проверено документов "+str(checked)+
                     ", подтверждённых нарушений "+str(violations)))
            if (key.startswith("manual:") and
                status=="done" and old_status!="done" and
                checked>old_checked and checked>0):
                # Job 'done' means EIS intake finished, not a certified
                # complete legal review. Use the accurate narrower statement.
                db.execute(
                    """INSERT INTO account_alert_events
                      (user_id,source_key,kind,title,detail) VALUES(%s,%s,%s,%s,%s)""",
                    (owner,key,"documents_checked",
                     "Завершена загрузка документов ЕИС",
                     snapshot["label"]+": прочитано "+str(checked)+" документов"))
            if (key.startswith("thematic:") and
                status=="completed" and old_status!="completed"):
                db.execute(
                    """INSERT INTO account_alert_events
                      (user_id,source_key,kind,title,detail) VALUES(%s,%s,%s,%s,%s)""",
                    (owner,key,"thematic_finished","Тематическая проверка завершена",
                     "Подтверждено нарушений: "+str(violations)))
        db.execute(
            """INSERT INTO account_alert_snapshots
               (user_id,source_key,status,violation_count,documents_checked)
               VALUES(%s,%s,%s,%s,%s)
               ON CONFLICT(user_id,source_key) DO UPDATE SET
                status=EXCLUDED.status,
                violation_count=GREATEST(account_alert_snapshots.violation_count,
                                         EXCLUDED.violation_count),
                documents_checked=GREATEST(account_alert_snapshots.documents_checked,
                                          EXCLUDED.documents_checked),
                observed_at=CURRENT_TIMESTAMP""",
            (owner,key,status,violations,checked))


@router.get("/notifications")
def notifications(request:Request, response:Response):
    owner=current_account(request)[0]
    response.headers.update(no_store_headers())
    try:
        snapshots=_observations(request,owner)
        with connect() as db:
            _reconcile_alerts(db,owner,snapshots)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503,"Не удалось обновить уведомления") from exc
    with connect() as db:
        rows=db.execute(
            """SELECT id,source_key,kind,title,detail,created_at,read_at
               FROM account_alert_events WHERE user_id=%s
               ORDER BY id DESC LIMIT 80""",(owner,)).fetchall()
    return [{"id":row[0],"source_key":row[1],"kind":row[2],
             "title":row[3],"detail":row[4],
             "created_at":row[5].isoformat(),
             "read":row[6] is not None} for row in rows]


@router.post("/notifications/{event_id}/read")
def notification_read(event_id:int, request:Request):
    require_origin(request)
    owner=current_account(request)[0]
    if event_id<=0:
        raise HTTPException(422,"Некорректное уведомление")
    with connect() as db:
        row=db.execute(
            """UPDATE account_alert_events SET read_at=COALESCE(read_at,CURRENT_TIMESTAMP)
               WHERE user_id=%s AND id=%s RETURNING id""",
            (owner,event_id)).fetchone()
    if not row:
        raise HTTPException(404,"Уведомление не найдено")
    return {"read":True}


def _verify_odt(data):
    if not isinstance(data,bytes) or not 1024<len(data)<=MAX_MEMO:
        raise HTTPException(422,"Недопустимый размер служебной записки ODT")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if archive.read("mimetype").strip()!=b"application/vnd.oasis.opendocument.text":
                raise ValueError("not odt")
            if not archive.getinfo("content.xml").file_size:
                raise ValueError("empty ODT")
    except (zipfile.BadZipFile,KeyError,ValueError) as exc:
        raise HTTPException(422,"Сгенерированный документ не является ODT") from exc
    return data


def _pdf_from_odt(data):
    """Bounded, no-network conversion; never make or claim a fake PDF."""
    converter=shutil.which("libreoffice") or shutil.which("soffice")
    if not converter:
        raise HTTPException(503,"Конвертация PDF временно недоступна")
    with tempfile.TemporaryDirectory(prefix="eis-memo-pdf-") as temp:
        directory=Path(temp)
        src=directory/"memo.odt"
        src.write_bytes(_verify_odt(data))
        profile=(directory/"lo_profile").as_uri()
        try:
            result=subprocess.run(
                [converter,"-env:UserInstallation="+profile,
                 "--headless","--convert-to","pdf:writer_pdf_Export",
                 "--outdir",str(directory),str(src)],
                stdin=subprocess.DEVNULL,capture_output=True,timeout=40,check=False)
        except (OSError,subprocess.TimeoutExpired) as exc:
            raise HTTPException(503,"Ошибка конвертации PDF") from exc
        output=directory/"memo.pdf"
        if result.returncode or not output.is_file():
            raise HTTPException(503,"LibreOffice не смог преобразовать записку в PDF")
        if output.stat().st_size>MAX_PDF:
            raise HTTPException(413,"Размер PDF превышает ограничение")
        pdf=output.read_bytes()
        if not pdf.startswith(b"%PDF-"):
            raise HTTPException(503,"Ошибка формата PDF")
        return pdf


@router.get("/memo-archive")
def memo_archive(request:Request, response:Response):
    owner=current_account(request)[0]
    response.headers.update(no_store_headers())
    with connect() as db:
        rows=db.execute(
            """SELECT m.id,m.reg_number,m.created_at,
                      (b.memo_id IS NOT NULL) AS downloadable,
                      (b.pdf IS NOT NULL) AS has_pdf
               FROM account_memos m LEFT JOIN account_memo_blobs b ON b.memo_id=m.id
               WHERE m.user_id=%s
               ORDER BY m.created_at DESC LIMIT 500""",(owner,)).fetchall()
    return [{"id":str(row[0]),"reg_number":row[1],
             "created_at":row[2].isoformat(),"downloadable":bool(row[3]),
             "has_pdf":bool(row[4])} for row in rows]


class MemoRequisites(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recipient: str = Field(max_length=199)
    executor: str = Field(max_length=199)
    phone: str | None = Field(default=None,max_length=199)

    @field_validator("recipient", "executor", "phone")
    @classmethod
    def printable(cls, value):
        if value is None:return None
        if any(ord(c)<32 and c not in "\n\t\r" for c in value):
            raise ValueError("Недопустимые управляющие символы")
        return " ".join(value.split())


def _memo_requisites(owner, reg_number):
    with connect() as db:
        row=db.execute(
            "SELECT recipient,executor,phone FROM account_memo_requisites "
            "WHERE user_id=%s AND reg_number=%s",(owner,reg_number)).fetchone()
    return ({"recipient":row[0],"executor":row[1],**({"phone":row[2]} if row[2] is not None else {})} if row else {})


def _memo_defaults():
    # Read only the two editable lines; never change the shared official template.
    from v304_official_memo_odt import template_path, OO, TX, _text
    from xml.etree import ElementTree as ET
    path=template_path()
    if not path.is_file():return {}
    try:
        with zipfile.ZipFile(path) as z:
            root=ET.fromstring(z.read("content.xml"))
        body=root.find(".//{"+OO+"}text")
        paragraphs=list(body.iter("{"+TX+"}p")) if body is not None else []
        return {key:_text(paragraphs[idx]).strip() for key,idx in
                (("recipient",13),("executor",55),("phone",56)) if idx<len(paragraphs)}
    except (OSError,KeyError,zipfile.BadZipFile,ET.ParseError):
        return {}


@router.get("/memo-requisites/{reg_number}")
def get_memo_requisites(reg_number:str,request:Request,response:Response):
    owner=current_account(request)[0]
    if not REG.fullmatch(reg_number):raise HTTPException(422,"Неверный номер закупки ЕИС")
    response.headers.update(no_store_headers())
    fields=_memo_requisites(owner,reg_number)
    return {"fields":{**_memo_defaults(),**fields},"saved":bool(fields)}


@router.put("/memo-requisites/{reg_number}")
def save_memo_requisites(reg_number:str,data:MemoRequisites,request:Request):
    require_origin(request)
    owner=current_account(request)[0]
    if not REG.fullmatch(reg_number):raise HTTPException(422,"Неверный номер закупки ЕИС")
    with connect() as db:
        db.execute(
            """INSERT INTO account_memo_requisites(user_id,reg_number,recipient,executor,phone)
               VALUES(%s,%s,%s,%s,%s) ON CONFLICT(user_id,reg_number) DO UPDATE SET
               recipient=EXCLUDED.recipient,executor=EXCLUDED.executor,
               phone=COALESCE(EXCLUDED.phone,account_memo_requisites.phone),
               updated_at=CURRENT_TIMESTAMP""",
            (owner,reg_number,data.recipient,data.executor,data.phone))
    return {"saved":True,"fields":data.model_dump(exclude_none=True)}


@router.post("/memo-archive/{reg_number}", status_code=201)
async def create_archived_memo(reg_number:str,request:Request):
    require_origin(request)
    owner=current_account(request)[0]
    if not REG.fullmatch(reg_number):
        raise HTTPException(422,"Неверный номер закупки ЕИС")
    builder=getattr(request.app.state,"eis223_build_memo",None)
    if not callable(builder):
        raise HTTPException(503,"Формирование ODT пока недоступно")
    with connect() as db:
        number=db.execute(
            "SELECT COUNT(*) FROM account_memos WHERE user_id=%s",(owner,)).fetchone()[0]
    if number>=100:
        raise HTTPException(409,"Личный архив содержит 100 записок; обратитесь к администратору")
    # The existing official renderer rejects unverified purchases / missing
    # original template; never create a placeholder document or fake finding.
    rendered=await builder(reg_number,fields=_memo_requisites(owner,reg_number))
    binary=_verify_odt(rendered.body)
    memo_id=uuid4()
    with connect() as db:
        db.execute(
            """INSERT INTO account_memos(id,user_id,reg_number,storage_key)
               VALUES(%s,%s,%s,%s)""",
            (memo_id,owner,reg_number,"database:odt"))
        db.execute(
            "INSERT INTO account_memo_blobs(memo_id,odt) VALUES(%s,%s)",
            (memo_id,binary))
    return {"id":str(memo_id),"reg_number":reg_number,"downloadable":True}


def _owned_blob(memo_id,owner):
    with connect() as db:
        row=db.execute(
            """SELECT m.reg_number,b.odt,b.pdf FROM account_memos m
               LEFT JOIN account_memo_blobs b ON b.memo_id=m.id
               WHERE m.id=%s AND m.user_id=%s""",(memo_id,owner)).fetchone()
    if row is None:
        raise HTTPException(404,"Служебная записка не найдена")
    if row[1] is None:
        raise HTTPException(409,
            "Старый документ не был сохранён в архиве; создайте новую записку из проверенных данных")
    return row


async def download_owned_memo(memo_id:UUID,request:Request,format:str="odt"):
    owner=current_account(request)[0]
    if format not in ("odt","pdf"):
        raise HTTPException(422,"Доступны только ODT и PDF")
    reg,odt,pdf=_owned_blob(memo_id,owner)
    if format=="odt":
        binary=bytes(odt)
        mime="application/vnd.oasis.opendocument.text"
    else:
        binary=bytes(pdf) if pdf is not None else await asyncio.to_thread(
            _pdf_from_odt,bytes(odt))
        if pdf is None:
            with connect() as db:
                db.execute(
                    """UPDATE account_memo_blobs SET pdf=%s,
                       pdf_created_at=CURRENT_TIMESTAMP
                       WHERE memo_id=%s AND EXISTS
                       (SELECT 1 FROM account_memos m WHERE m.id=%s AND m.user_id=%s)""",
                    (binary,memo_id,memo_id,owner))
        mime="application/pdf"
    return Response(binary,media_type=mime,
        headers={"Cache-Control":"private,no-store",
                 "X-Content-Type-Options":"nosniff",
                 "Content-Disposition":f'attachment; filename="memo_{reg}.{format}"'})


@router.get("/memo-archive/{memo_id}/{format}")
async def archive_download(memo_id:UUID,format:str,request:Request):
    return await download_owned_memo(memo_id,request,format)
