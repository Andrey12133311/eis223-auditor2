"""V351: throughput, expanded queue, strict original proof and dedup smoke tests."""
import asyncio
import sqlite3
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

import v332_all_procurements_audit as general
import v342_thematic_queue as theme
import v345_thematic_accelerator as fast

REG="32615969056"
URL="https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid="+"A"*32

def connection(path):
    db=sqlite3.connect(path,check_same_thread=False,timeout=4)
    db.row_factory=sqlite3.Row
    return db

async def file_concurrency():
    with tempfile.TemporaryDirectory() as home:
        root=Path(home)
        files=[root/("file"+str(i)+".pdf") for i in range(8)]
        for i,p in enumerate(files):p.write_bytes(("Test official EIS "+str(i)).encode())
        rows=[{"id":i+1,"name":"File "+str(i),"local_path":str(p),
            "url":URL,"metadata_json":"{}","status":"checked"}
            for i,p in enumerate(files)]
        rows.insert(3,{"id":30,"name":"Missing","local_path":str(root/"missing.pdf")})
        threads=threading.Lock()
        parallel=[0,0]
        def extract(path,extractor):
            with threads:
                parallel[0]+=1
                parallel[1]=max(parallel[1],parallel[0])
            try:
                time.sleep(.06)
                return "Verified contents from "+path.name
            finally:
                with threads:parallel[0]-=1
        with patch.object(general,"root",return_value=root), \
             patch.object(general,"source_verified",return_value=True), \
             patch.object(general,"document_text",side_effect=extract):
            result,unreadable=await general.stored_docs(
                {"extract_text":lambda path:"unused"},REG,rows)
        assert unreadable==1,(unreadable,result)
        assert len(result)==8 and [x["name"] for x in result]==[
            "File "+str(i) for i in range(8)]
        assert 1<parallel[1]<=4,parallel
        assert all(x["status"]=="checked" for x in result)
        assert all(x["meta"]["source"]!="existing_local_unverified" for x in result)
        return parallel[1]

def more_verified_purchases():
    with tempfile.TemporaryDirectory() as home:
        path=Path(home)/"queue.db"
        db=connection(path)
        db.executescript("""CREATE TABLE purchases(
          reg_number TEXT PRIMARY KEY,customer TEXT,last_seen TEXT);
        CREATE TABLE v319_manual_inputs(reg TEXT PRIMARY KEY);
        CREATE TABLE v332_audit_queue(campaign TEXT NOT NULL,reg TEXT NOT NULL,
          state TEXT NOT NULL DEFAULT 'pending',PRIMARY KEY(campaign,reg));""")
        db.execute("INSERT INTO purchases VALUES(?,'ООО Проверка','2026-10-15')",(REG,))
        manual="32616499999"
        another="32616488888"
        db.execute("INSERT INTO purchases VALUES(?,'АО Новая','2026-10-15')",(another,))
        db.execute("INSERT INTO purchases VALUES(?,'ООО Ручная','2026-10-15')",(manual,))
        db.execute("INSERT INTO v319_manual_inputs VALUES(?)",(manual,))
        db.execute("INSERT INTO v332_audit_queue(campaign,reg) VALUES(?,?)",(general.CAMPAIGN,REG))
        db.commit();db.close()
        ns={"conn":lambda:connection(path),"_verified_set235":lambda:[REG,manual,another]}
        assert set(general.dashboard_regs(ns,include_new=True))=={REG,another}
        assert general.add_new_verified(ns)==1
        assert general.add_new_verified(ns)==0
        db=connection(path)
        assert [x[0] for x in db.execute(
            "SELECT reg FROM v332_audit_queue ORDER BY reg")]==sorted([REG,another])
        db.close()

async def thematic_legal_source_gate():
    fake_row={"reg_number":REG,"customer":"ООО Тест"}
    verified=[{"name":"Оригинал.pdf","text":"Извещение и разъяснение",
      "status":"checked","meta":{"source":"official_eis_original",
       "source_url":URL,"sha256":"a"*64}}]
    positive=[
      {"kind":"violation","code":"223-3.2-3-CLARIFICATION-CROSS-DOC-LATE",
       "title":"Разъяснение опубликовано с нарушением срока",
       "law":"223-ФЗ, ст. 3.2 ч. 3","document":"Оригинал.pdf",
       "source_url":URL,"evidence":"Оригинал разъяснения опубликован 12.10.2026"},
      {"kind":"violation","code":"OTHER-UNTRUSTED","title":"Нарушение сроков",
       "law":"223-ФЗ","document":"Фальшивый.pdf",
       "source_url":"https://not-eis.example/fake","evidence":"Без подлинника"}
    ]
    with patch.object(general,"existing",return_value=(fake_row,[])),\
         patch.object(general,"as_procurement",return_value={"reg":REG,"customer":"ООО Тест"}),\
         patch.object(general,"stored_docs",return_value=(verified,0)),\
         patch.object(general,"is_official",return_value=True):
        state,result=await theme.independent_review(
          {"analyze":lambda p,docs:positive},REG,["dates"])
        assert state=="completed" and len(result)==1,(state,result)
        assert "разъяснение" in result[0]["title"].lower()
        assert result[0]["document"]=="Оригинал.pdf"
        assert result[0]["source_url"]==URL
    with patch.object(general,"existing",return_value=(fake_row,[])),\
         patch.object(general,"as_procurement",return_value={"reg":REG,"customer":"ООО Тест"}),\
         patch.object(general,"stored_docs",return_value=(verified,0)),\
         patch.object(general,"is_official",return_value=True):
        state,result=await theme.independent_review(
          {"analyze":lambda p,docs:positive},REG,["national"])
        assert state=="completed" and result==[]

def thematic_dedup():
    with tempfile.TemporaryDirectory() as home:
        path=Path(home)/"thematic.db"
        ns={"conn":lambda:connection(path)}
        theme.init(ns)
        fast.install(ns)
        batch="a"*24
        db=connection(path)
        db.execute("INSERT INTO thematic_batches(id,categories,created_at,status,legal_forms) VALUES(?,?,?,?,?)",
                   (batch,'["sme"]',"2026-10-15","active",'["ООО"]'))
        db.execute("INSERT INTO thematic_items(batch_id,reg,updated_at) VALUES(?,?,?)",
                   (batch,REG,"2026-10-15"))
        db.commit();db.close()
        text=("Расчет производится на основании счета в течение 15 рабочих дней "
              "с даты подписания Заказчиком документов о приемке товара.")
        a={"category":"sme","title":"Срок оплаты для МСП превышен",
           "law":"ПП РФ № 1352 п. 28","code":"SME-15","document":"Договор.pdf",
           "source_url":URL,"evidence":"Пункт 9. "+text}
        b={**a,"evidence":text}
        item={"batch":batch,"reg":REG,"ready":2,"doc_id":2}
        fast._persist(ns,[(item,"completed",[a,b],"Готово")])
        fast._persist(ns,[(item,"completed",[b],"Готово")])
        db=connection(path)
        result=db.execute("SELECT code,document,source_url,law_url FROM thematic_findings").fetchall()
        assert len(result)==1,result
        assert result[0]["document"]=="Договор.pdf" and result[0]["source_url"]==URL
        assert db.execute("SELECT status FROM thematic_items").fetchone()[0]=="completed"
        db.close()

async def run():
    parallel=await file_concurrency()
    more_verified_purchases()
    await thematic_legal_source_gate()
    thematic_dedup()
    print("V351_CAPACITY_SELFTEST_OK parallel_extraction="+str(parallel)+
          " verified_queue_expands=True manual_excluded=True"+
          " thematic_full_engine=True fake_source_rejected=True"+
          " same_episode_not_duplicated=True originals_untouched=True")

if __name__=="__main__":
    asyncio.run(run())
