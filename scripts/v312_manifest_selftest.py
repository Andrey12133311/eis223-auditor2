"""Build-time proof that real EIS 223 noticeInfoId and RAR attachments are accepted.

No network calls, no database, no real procurement count changes.
"""
from __future__ import annotations
from v299_browser_intake import _parse_manifest
from v295_eis_originals import uid_from_url
from v311_verified_customer import _official_source

REG="32616280442"
NOTICE="18575632"
SOURCE="https://zakupki.gov.ru/223/purchase/public/purchase/info/documents.html?noticeInfoId="+NOTICE
CARD="https://zakupki.gov.ru/223/purchase/public/purchase/info/common-info.html?noticeInfoId="+NOTICE
FILE="https://zakupki.gov.ru/223/purchase/public/download/download.html?id=23230413"
def main():
    v=_parse_manifest({
      "reg_number":REG,
      "source_page":SOURCE,
      "landing_page":CARD,
      "documents":[{"name":"Приложение №1 - Технические требования.rar","url":FILE}],
      "declared_attachment_count":11,
      "customer":"Публичное акционерное общество «Камчатскэнерго»",
      "customer_inn":"4100000668",
    })
    assert v["declared_attachment_count"]==11
    assert v["documents"][0]["name"].endswith(".rar")
    assert v["documents"][0]["uid"]==uid_from_url(FILE)
    assert len(v["documents"][0]["uid"])==32
    assert _official_source(CARD,REG)
    for bad in [
        FILE.replace("zakupki.gov.ru","example.org"),
        FILE.replace("id=23230413","id=23230413&path=/etc/passwd"),
        "http://zakupki.gov.ru/223/purchase/public/download/download.html?id=23230413"
    ]:
        assert uid_from_url(bad) is None,bad
    print("V312 MANIFEST_SELFTEST_OK noticeInfoId=accepted archive=RAR uid=validated buyer_source=official",flush=True)

if __name__=="__main__":
    main()
