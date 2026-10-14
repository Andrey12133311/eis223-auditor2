"""Non-mutating fixture tests for 223-FZ part 11 amendment deadlines."""
from datetime import date
import v330_change_timelines as rules

def main():
    target={"reg":"32616233661","submission_end":"2026-09-10"}
    doc={
      "name":"Изменение к документации.pdf",
      "url":"https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid=F6B72FE07FD247EAB63270BD21F72A1B",
      "status":"checked","type":"documentation",
      "text":"Решение от 10.08.2026 о внесении изменений в документацию "
             "закупки. Срок подачи заявок подлежит продлению. "*3,
      "meta":{"source":"official_eis_archive_member","sha256":"a"*64,
              "source_url":"https://zakupki.gov.ru/223/filestore/public/1.0/download/fz223/file.html?uid=F6B72FE07FD247EAB63270BD21F72A1B"}
    }
    assert rules.parse_day("10.08.2026")==date(2026,8,10)
    assert len(rules._decision_sources([doc]))==1
    saved=rules._official_events
    try:
        rules._official_events=lambda reg:[]
        assert not rules.findings(target,[doc])
        assert len(rules.checks(target,[doc]))==1
        rules._official_events=lambda reg:[{
          "day":date(2026,9,2),
          "url":"https://zakupki.gov.ru/223/purchase/public/purchase/info/changes.html?noticeId=5555",
          "evidence":"Изменения документации; размещено 02.09.2026",
          "decision_date":date(2026,8,10)
        }]
        f=rules.findings(target,[doc])
        assert len(f)==1 and f[0]["kind"]=="violation"
        assert "23 календарных дней" in f[0]["evidence"],f
        assert f[0]["code"]=="223-4-11-AMENDMENT-PUBLICATION-LATE"
        rules._official_events=lambda reg:[{
          "day":date(2026,8,12),
          "url":"https://zakupki.gov.ru/223/purchase/public/purchase/info/changes.html?noticeId=5555",
          "evidence":"Изменения размещены 12.08.2026",
          "decision_date":date(2026,8,10)
        }]
        assert not rules.findings(target,[doc])
        # A date on a user-supplied document without a source does not count.
        rules._official_events=lambda reg:[{
          "day":date(2026,9,2),
          "url":"https://zakupki.gov.ru/223/purchase/public/purchase/info/changes.html?noticeId=5555",
          "evidence":"Изменения размещены 02.09.2026",
          "decision_date":date(2026,8,10)
        }]
        unverified={**doc,"meta":{"source":"existing_local_unverified","sha256":"a"*64}}
        assert not rules.findings(target,[unverified])
        # Decision date derived from the wrong revision must not be paired.
        rules._official_events=lambda reg:[{
          "day":date(2026,9,2),"decision_date":date(2026,8,11),
          "url":"https://zakupki.gov.ru/223/purchase/public/purchase/info/changes.html?noticeId=5555",
          "evidence":"Изменения опубликованы 02.09.2026"
        }]
        assert not rules.findings(target,[doc])
        print("V330_SELFTEST_OK verified_only=True dates_23_days=True "
              "missing_EIS_review=True mismatch_rejected=True calendar_days=True",flush=True)
    finally:rules._official_events=saved
if __name__=="__main__":main()
