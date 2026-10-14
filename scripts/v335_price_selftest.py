"""Regression checks for real EIS price layouts and independent field recovery."""
from __future__ import annotations
import json
from pathlib import Path
import sqlite3
import tempfile

from fastapi import FastAPI
from v311_verified_customer import _initialize,_save_verified_price,_verified_money
from v324_official_price import prices_in_text,verified_nmcd
from v335_price_recovery import price_changes,repair_cached,install,patch_html,card_price_html,cached_evidence
from v299_browser_intake import _known

REG='32616471929'
URL='https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid='+'A'*32
CARD='https://zakupki.gov.ru/223/purchase/public/purchase/info/common-info.html?regNumber='+REG


def doc(text):
    return {'name':'Извещение.doc','type':'notice','status':'checked','text':text,
            'meta':{'source':'official_eis_original','sha256':'b'*64,'source_url':URL}}


def run():
    # Actual field from EIS procurement 32616471929: the extra lot-price
    # parenthesis and sum on the next line previously yielded no amount.
    notice=('4. Начальная (максимальная) цена договора (цена лота):\n'
            '41 081 (Сорок одна тысяча восемьдесят один) руб. 60 коп.\n'
            'Начальная максимальная цена определена методом сопоставимых рыночных цен.')
    result=verified_nmcd([doc(notice)])
    assert result['price_num']==41081.60,result
    assert '41 081' in result['evidence'] and '60 коп' in result['evidence']
    stored={'price':'41081.60','price_source_url':URL,'price_evidence':result['evidence']}
    assert cached_evidence({'card_fields_json':json.dumps({'metadata_evidence':{'customer':'ООО «Заказчик»'},'price_evidence':stored})},None)==stored
    stale={**stored,'price':'14.10','price_evidence':'Дата подготовки обоснования НМЦД:14.10.2026'}
    assert cached_evidence({'card_fields_json':json.dumps({'price_evidence':stale})},None)=={}
    wrapped=('|7. |Начальная (максимальная) |41 081 (Сорок одна тысяча |\n'
             '|   |цена договора (цена лота)|восемьдесят один) руб. 60 коп. |')
    assert verified_nmcd([doc(wrapped)])['price_num']==41081.60
    maximum=verified_nmcd([doc('Максимальное значение цены договора составляет: '
        '2 000 000,00 руб. Цена единицы товара составляет: 427 000 руб.')])
    assert maximum['price_num']==2000000 and maximum['price_kind']=='maximum_contract_value'
    assert prices_in_text('Начальная (максимальная) цена договора: 5 процентов')==[]
    assert prices_in_text('Начальная (максимальная) цена договора не должна превышать 7 млн. рублей')==[]
    assert prices_in_text('Начальная (максимальная) цена договора: 4 рабочих дня')==[]
    assert prices_in_text('НМЦД: -100 руб.')==[]
    assert prices_in_text('Дата подготовки обоснования НМЦД:14.10.2026')==[]
    assert prices_in_text('3. Обоснование НМЦД; 4. Рекомендуемая форма заявки участника закупки;')==[]
    assert prices_in_text('14. ОБОСНОВАНИЕ НАЧАЛЬНОЙ (МАКСИМАЛЬНОЙ) ЦЕНЫ ДОГОВОРА 25')==[]
    assert prices_in_text('НМЦД\n2.1. Обоснование цены')==[]
    assert verified_nmcd([doc('НМЦД: 100 руб.'),doc('НМЦД: 200 руб.')]).get('price_conflict')
    assert _verified_money('1\u202f234\u00a0567,89 Российский рубль')[1]==1234567.89
    assert _verified_money(0)[1]==0
    for value in (True,False,'NaN','Infinity','100-200','Нет данных'):
        try:_verified_money(value)
        except ValueError:pass
        else:raise AssertionError('Malformed monetary value accepted: '+repr(value))
    assert price_changes({'price':'123 456,78 ₽','price_num':None})=={'price_num':123456.78}
    assert price_changes({'price':None,'price_num':250000})=={'price':'250000.00'}
    assert price_changes({'price':'—','price_num':None},{'price':'41081.60'})=={'price':'41081.60','price_num':41081.60}
    assert price_changes({'price':'100','price_num':100},{'price':'999'})=={}
    assert price_changes({'price':'100','price_num':200},{'price':'999'})=={}

    with tempfile.TemporaryDirectory(prefix='v335-price-') as tmp:
        path=Path(tmp)/'app.sqlite'
        def conn():
            db=sqlite3.connect(path);db.row_factory=sqlite3.Row;return db
        db=conn()
        db.executescript('''CREATE TABLE purchases(reg_number TEXT PRIMARY KEY,
            price TEXT,price_num REAL,card_fields_json TEXT,customer TEXT,customer_inn TEXT,
            title TEXT,last_seen TEXT,documents_checked INTEGER);
            CREATE TABLE findings(reg_number TEXT,title TEXT);
            CREATE TABLE checks(reg_number TEXT,item TEXT,status TEXT,evidence TEXT);
            CREATE TABLE docs(reg_number TEXT,name TEXT);''')
        db.execute("INSERT INTO findings VALUES(?,?)",(REG,'Сохранённое нарушение'))
        db.execute("INSERT INTO checks VALUES(?,?,?,?)",(REG,'Контроль срока','violation','Нарушение срока'))
        db.execute("INSERT INTO checks VALUES(?,?,?,?)",(REG,'НМЦД','check','Не найдено'))
        db.execute("INSERT INTO docs VALUES(?,?)",(REG,'Извещение.doc'))
        db.execute("INSERT INTO purchases VALUES(?,?,?,?,?,?,?,?,?)",(REG,'—',None,'{}','ООО «Заказчик»','',
            'Поставка лекарственных средств','2026-10-14',3))
        db.commit();db.close()
        ns={'conn':conn,'app':FastAPI()}
        _initialize(ns)
        assert _known(ns,[REG])[REG]['needs_metadata']
        assert _save_verified_price(ns,REG,'41081.60',41081.60,CARD)
        db=conn();row=dict(db.execute('SELECT * FROM purchases WHERE reg_number=?',(REG,)).fetchone())
        assert row['price_num']==41081.60 and row['price']=='41081.60'
        assert tuple(db.execute("SELECT status,evidence FROM checks WHERE item='НМЦД'").fetchone())==('ok','41\u00a0081,60 ₽')
        assert json.loads(row['card_fields_json'])['summary']['price_num']==41081.60
        assert not _known(ns,[REG])[REG]['needs_metadata']
        # Two independently missing fields were not repaired by the old AND condition.
        db.execute('UPDATE purchases SET price=NULL,price_num=250000 WHERE reg_number=?',(REG,));db.commit();db.close()
        assert _save_verified_price(ns,REG,'999',999,CARD)
        db=conn();row=db.execute('SELECT price,price_num FROM purchases').fetchone()
        assert tuple(row)==('250000.00',250000)
        db.execute("UPDATE purchases SET price='123 456,78 ₽',price_num=NULL");db.commit();db.close()
        assert repair_cached(ns)==1
        assert repair_cached(ns)==0
        db=conn()
        assert db.execute('SELECT price_num FROM purchases').fetchone()[0]==123456.78
        assert db.execute('SELECT COUNT(*) FROM findings').fetchone()[0]==1
        assert tuple(db.execute("SELECT status,evidence FROM checks WHERE item='Контроль срока'").fetchone())==('violation','Нарушение срока')
        assert db.execute('SELECT COUNT(*) FROM checks').fetchone()[0]==2
        assert db.execute('SELECT COUNT(*) FROM docs').fetchone()[0]==1
        db.close()
        saved=[]
        ns['save']=lambda *args:saved.append(args)
        install(ns)
        p={'reg':REG,'price':'','price_num':None}
        ns['save'](p,[],[],[],{'summary':{}})
        assert p['price_num']==123456.78 and p['price']=='123456.78'
        assert saved[0][4]['summary']['price_num']==123456.78
        assert '123\u00a0456,78 ₽' in card_price_html(ns,REG)
    html=patch_html('<head></head><script>money(x.price_num);money(p.price_num);</script>')
    assert 'window.eis223PriceText(x)' in html and 'window.eis223PriceText(p)' in html
    assert patch_html(html)==html
    print('V335_PRICE_SELFTEST_OK actual_notice_41081_60=True separate_fields=True '
          'preserve_existing=True zero_not_missing=True no_violation_changes=True')


if __name__=='__main__':run()
