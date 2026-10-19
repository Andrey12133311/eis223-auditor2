"""Private memo settings persist by authenticated account and procurement."""
import asyncio
import io
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import zipfile
from xml.etree import ElementTree as ET

import unittest
from fastapi import Response, HTTPException
from pydantic import ValidationError
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from scripts import v367_private_features as memo
from scripts.account_pages_v1 import MEMOS
from scripts.account_schema_v1 import SCHEMA
from v304_official_memo_odt import _render_odt, OO, TX
from memo_fixture import memo_template

REG='32616412317'
OTHER='32616412318'

class DB:
    def __init__(self):self.fields={};self.blobs=[];self.row=None
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def execute(self,sql,params):
        if 'INSERT INTO account_memo_requisites' in sql:
            assert 'ON CONFLICT(user_id,reg_number)' in sql
            self.fields[params[:2]]=params[2:];self.row=None
        elif 'SELECT recipient,executor' in sql:
            assert 'WHERE user_id=%s AND reg_number=%s' in sql
            self.row=self.fields.get(params)
        elif 'SELECT COUNT(*)' in sql:self.row=(0,)
        elif 'INSERT INTO account_memo_blobs' in sql:self.blobs.append(params[1])
        elif 'INSERT INTO account_memos' not in sql:raise AssertionError(sql)
        return self
    def fetchone(self):return self.row


def test_owner_case_persistence_and_roles():
    db=DB()
    with patch.object(memo,'connect',return_value=db),patch.object(memo,'require_origin'),patch.object(memo,'_memo_defaults',return_value={'recipient':'Template','executor':'Default'}):
        for owner,role in [('a','user'),('b','manager'),('c','admin')]:
            with patch.object(memo,'current_account',return_value=(owner,role,'approved')):
                fields=memo.MemoRequisites(recipient='To '+owner,executor='By '+owner)
                memo.save_memo_requisites(REG,fields,object())
                assert memo.get_memo_requisites(REG,object(),Response())['fields']==fields.model_dump(exclude_none=True)
                assert not memo.get_memo_requisites(OTHER,object(),Response())['saved']
        with patch.object(memo,'current_account',return_value=('a','user','approved')):
            assert memo.get_memo_requisites(REG,object(),Response())['fields']['recipient']=='To a'
            memo.save_memo_requisites(REG,memo.MemoRequisites(recipient='',executor=''),object())
            assert memo.get_memo_requisites(REG,object(),Response())['fields']=={'recipient':'','executor':''}
        assert db.fields[('b',REG)]==('To b','By b',None)


def test_validation_and_origin():
    for fields in ({'recipient':'x'*200,'executor':'a'}, {'recipient':'x','executor':'a','user_id':'other'}, {'recipient':'x\x00','executor':'a'}):
        with unittest.TestCase().assertRaises(ValidationError):memo.MemoRequisites(**fields)
    with patch.object(memo,'require_origin',side_effect=HTTPException(403,'Origin')),patch.object(memo,'connect') as db:
        with unittest.TestCase().assertRaises(HTTPException):memo.save_memo_requisites(REG,memo.MemoRequisites(recipient='x',executor='a'),object())
        db.assert_not_called()


def test_saved_values_used_for_export():
    db=DB();db.fields[('a',REG)]=('Recipient A','Executor A',None);db.fields[('b',REG)]=('B','B',None)
    builder=AsyncMock(return_value=Response(b'valid-odt'))
    request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(eis223_build_memo=builder)))
    with patch.object(memo,'connect',return_value=db),patch.object(memo,'current_account',return_value=('a','user','approved')),patch.object(memo,'require_origin'),patch.object(memo,'_verify_odt',side_effect=lambda b:b):
        asyncio.run(memo.create_archived_memo(REG,request))
    builder.assert_awaited_once_with(REG,fields={'recipient':'Recipient A','executor':'Executor A'})
    assert db.blobs==[b'valid-odt']


def fixture():
    # Exercise the renderer's actual accepted source layout while keeping the
    # distinctive requisite values used by these persistence/export checks.
    with zipfile.ZipFile(io.BytesIO(memo_template())) as z:
        items={name:z.read(name) for name in z.namelist()}
    root=ET.fromstring(items['content.xml'])
    body=root.find('.//{'+OO+'}text')
    paragraphs=list(body.iter('{'+TX+'}p'))
    for index,value in {13:'Template recipient',33:'Signature name',
                        55:'Default executor',56:'Default phone'}.items():
        paragraphs[index].text=value
    items['content.xml']=ET.tostring(root)
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:
        for name,binary in items.items():z.writestr(name,binary)
    return out.getvalue()


def test_odt_uses_edits_preserves_signature_defaults_and_clear():
    source=fixture()
    original=bytes(source)
    findings=[{'title':'Проверенное нарушение','law':'223-ФЗ','evidence':'Подтверждено документом','document':'Извещение'}]
    for fields in ({'recipient':'New recipient','executor':'New executor','phone':'555-0100'},{},{'recipient':'','executor':'','phone':''}):
        binary=_render_odt(source,{'reg_number':REG,'title':'Предмет закупки'},findings,'ООО «Пример»',fields)
        with zipfile.ZipFile(io.BytesIO(binary)) as z:
            text=' '.join(ET.fromstring(z.read('content.xml')).itertext())
        assert 'Signature name' in text and 'Заместитель начальника Управления' in text
        assert ('New recipient' in text)==bool(fields.get('recipient'))
        assert ('New executor' in text)==bool(fields.get('executor'))
        if not fields:assert 'Template recipient' in text and 'Default executor' in text and 'Default phone' in text
        if fields:
            assert 'Template recipient' not in text and 'Default executor' not in text and 'Default phone' not in text
            assert ('555-0100' in text)==bool(fields.get('phone'))
    assert source==original


def test_direct_exports_use_only_gate_identity_and_explicit_override():
    import tempfile
    import json
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import v304_official_memo_odt as renderer
    db=DB();db.fields[('a',REG)]=('Recipient A','Executor A',None)
    with tempfile.TemporaryDirectory() as tmp:
        template=Path(tmp)/'template.odt';template.write_bytes(fixture())
        app=FastAPI()
        with patch.object(renderer,'template_path',return_value=template),patch.object(renderer,'_read_purchase',return_value=({},[{}])),patch.object(renderer,'company_name',new=AsyncMock(return_value=('Organization','fixture'))),patch.object(renderer,'_render_odt',side_effect=lambda template,p,f,org,fields:json.dumps(fields).encode()),patch.object(memo,'connect',return_value=db):
            renderer.install({'app':app})
            @app.middleware('http')
            async def gate_identity(request,call_next):
                # Simulates identity supplied by the existing outer account gate.
                if request.headers.get('x-test-authenticated')=='yes':
                    request.scope['eis223_account_id']='a'
                return await call_next(request)
            with TestClient(app) as client:
                authenticated={'x-test-authenticated':'yes'}
                assert client.get('/api/v304/memo/'+REG+'.odt',headers=authenticated).json()=={'recipient':'Recipient A','executor':'Executor A'}
                assert client.get('/api/v304/memo/'+REG+'.odt?user_id=a').json()=={}
                assert client.post('/api/v218/memo/'+REG,headers=authenticated,json={'fields':{'executor':'Override'}}).json()=={'recipient':'Recipient A','executor':'Override'}
                assert db.fields[('a',REG)]==('Recipient A','Executor A',None)


def test_ui_and_schema_integrated():
    assert 'PRIMARY KEY(user_id,reg_number)' in SCHEMA
    assert 'id="memo-recipient"' in MEMOS and 'id="memo-executor"' in MEMOS
    assert 'await saveRequisites(reg)' in MEMOS
    assert 'sequence!==requisitesSequence' in MEMOS


def load_tests(loader,tests,pattern):
    return unittest.TestSuite(unittest.FunctionTestCase(fn) for name,fn in sorted(globals().items())
                              if name.startswith("test_") and callable(fn))

if __name__=="__main__":unittest.main()
