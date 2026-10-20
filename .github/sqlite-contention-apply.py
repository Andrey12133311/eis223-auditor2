"""One-use branch-only source transform; no network, runtime data or credentials."""
from pathlib import Path
import hashlib

HASHES = {
 'scripts/v314_eis_metadata.py': ('3eb8a87ed299af9aa8834ab51d484a5cae6671f8bf709fce12d9392a2629e3c6','096dd754ade4803223180b12e466f0856b678daab01b5283bfb153e5b16d692d'),
 'scripts/v298_eis_only.py': ('c59df0add6ad6374f2e46951ba0eb4f5cc062ab8f259a4b1d927aa9962739515','e0c413298a8d54e3071e7c2cf0284f8dda42e18371fbbcb8b464df477b3e56b8'),
 'scripts/full_recheck_runtime.py': ('24f345b4566fb06956d5de83d3ec0a42ff8ca677332b4c41cfc97751c2c2b6a8','9327158ead576ff62c88c6a0cb31d1dd3a4de2a848e6916aba9aa5f937af977d'),
 'scripts/v294_eis_bridge.py': ('e4b2b3425e430b4e9e153f8d1c6322ad0589f3c37fdeeddf5d8336dfae86c4da','a448a1e2440710977cfdeca51e4b4703257abae343a1bf81a937035eb2f560d3'),
 'Dockerfile': ('cbd523fbe9ad8ff8a6f446e0711f46e74b59080ac0a1e3db7872fc7c7ae74134','52a3d1cfa1e9bff75e7b478086689b32dd719c53fce4454b1debe39100c13edf'),
}
for name, (before, after) in HASHES.items():
    assert hashlib.sha256(Path(name).read_bytes()).hexdigest() == before, ('Unexpected input', name)

def replace_one(text, old, new):
    assert text.count(old) == 1, ('Expected exactly one patch location', old[:100])
    return text.replace(old, new)

p=Path('scripts/v314_eis_metadata.py'); s=p.read_text()
start=s.index('def apply_metadata('); end=s.index('\n\ndef stored_metadata',start)
s=s[:start]+Path('.github/metadata-contention.body').read_text().rstrip()+'\n'+s[end:]
p.write_text(s)

p=Path('scripts/v298_eis_only.py'); s=p.read_text()
old='''    db=ns["conn"]();db.row_factory=sqlite3.Row
    try:
        db.execute("BEGIN IMMEDIATE")
        columns={row[1] for row in db.execute("PRAGMA table_info(purchases)")}'''
new='''    # Duplicate SOAP deliveries and routine reg reports commonly reuse the
    # same revision. Do not compete for SQLite's writer for a read-only no-op.
    # Changed/new cards still recheck the revision in the transaction below.
    db=ns["conn"]();db.row_factory=sqlite3.Row
    try:
        cached=db.execute("SELECT * FROM purchases WHERE reg_number=?",(reg,)).fetchone()
        try:cached_card=json.loads(dict(cached).get("card_fields_json") or "{}") if cached is not None else {}
        except (ValueError,TypeError):cached_card={}
        prior=cached_card.get("soap_metadata") if isinstance(cached_card,dict) else None
        if isinstance(prior,dict) and revision_key(prior)>=revision_key(record):
            return {"stored":True,"changed":False,"reg_number":reg}
    finally:db.close()
    db=ns["conn"]();db.row_factory=sqlite3.Row
    try:
        db.execute("BEGIN IMMEDIATE")
        columns={row[1] for row in db.execute("PRAGMA table_info(purchases)")}'''
p.write_text(replace_one(s,old,new))

p=Path('scripts/full_recheck_runtime.py'); s=p.read_text()
old='''    from full_recheck_request import reopen_completed
    with transaction(ns) as c:
        return reopen_completed(c, request_id(ns), limit=CATCHUP_PAGE_SIZE, **_target_args(ns))'''
new='''    from full_recheck_request import reopen_completed
    # A late old-engine completion is handled on the next selector turn. The
    # common empty case is read-only; the ledger revalidates target + rows in
    # the writer transaction whenever preflight finds actual catch-up work.
    c = ns["conn"]()
    try:
        pending = c.execute("""SELECT 1 FROM full_recheck_members
            WHERE request_id=? AND status='completed' AND result_engine_signature<>? LIMIT 1""",
            (request_id(ns), _target_args(ns)["engine_signature"])).fetchone()
    finally:
        c.close()
    if pending is None:
        return 0
    with transaction(ns) as c:
        return reopen_completed(c, request_id(ns), limit=CATCHUP_PAGE_SIZE, **_target_args(ns))'''
p.write_text(replace_one(s,old,new))

p=Path('scripts/v294_eis_bridge.py'); s=p.read_text()
start=s.index('        was_new=not zip_path.exists()'); end=s.index('        metadata_import=',start)
block=s[start:end]
store='''def _store_archive(zip_path, json_path, raw, sha, reg, notices, unsupported_xmls):
    """Disk hashing/fsync must not hold the request event loop."""
'''+''.join(line[4:] if line.startswith('    ') else line for line in block.splitlines(True))+'    return was_new\n\n\n'
helpers=Path('.github/archive-contention.helpers').read_text()
split=helpers.index('def _registry_diagnostics(')
helpers=helpers[:split].rstrip()+'\n\n\n'+store+helpers[split:].rstrip()+'\n\n\n'
s=s[:start]+'        was_new=await _archive_io(_store_archive,zip_path,json_path,raw,sha,reg,notices,unsupported_xmls)\n'+s[end:]
position=s.index('def install(ns):'); s=s[:position]+helpers+s[position:]
old='''        try: notices=parse_notices(raw)
        except ValueError as exc:
            print(f"V294 ARCHIVE_REJECTED code={_rejection_code(exc)} bytes={total}",flush=True)
            raise HTTPException(422,str(exc)) from exc'''
new='''        try: notices=await _archive_io(parse_notices,raw)
        except ValueError as exc:
            code=_rejection_code(exc)
            print(f"V294 ARCHIVE_REJECTED code={code} bytes={total}",flush=True)
            headers={"X-EIS223-Rejection-Code":code}
            if code=="xml_registry_mismatch":
                # Invalid identity will not repair itself in the next five
                # minutes. Defer this job only, keeping other intake running.
                headers["Retry-After"]="1800"
                diagnostic=await _archive_io(_registry_diagnostics,raw)
                if diagnostic:
                    print("V294 XML_IDENTITY_DIAGNOSTIC "+json.dumps({
                        "archive_sha256":hashlib.sha256(raw).hexdigest(),
                        "members":diagnostic},sort_keys=True),flush=True)
            raise HTTPException(422,str(exc),headers=headers) from exc'''
p.write_text(replace_one(s,old,new))

p=Path('Dockerfile'); s=p.read_text()
s=replace_one(s,'tests/test_eis_response_size.py tests/', 'tests/test_eis_response_size.py tests/test_eis_archive_responsiveness.py tests/')
s=replace_one(s,'COPY tests/test_customer_name_completion.py tests/test_customer_metadata_lock_scope.py tests/', 'COPY tests/test_customer_name_completion.py tests/test_customer_metadata_lock_scope.py tests/test_ingestion_metadata_contention.py tests/')
s=replace_one(s,"python -m unittest discover -s tests -p 'test_customer_metadata_lock_scope.py'", "python -m unittest discover -s tests -p 'test_customer_metadata_lock_scope.py' && PYTHONPATH=/app:/app/scripts:/app/tests python -m unittest test_ingestion_metadata_contention")
p.write_text(s)

for name, (before, after) in HASHES.items():
    actual=hashlib.sha256(Path(name).read_bytes()).hexdigest()
    assert actual == after, ('Unexpected output',name,actual,after)
    print('VERIFIED_PATCH_FILE',name,actual)
