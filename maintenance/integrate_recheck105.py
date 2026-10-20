"""One-off isolated-branch integration; no production DB or secrets are used."""
from pathlib import Path
import hashlib
import json
import subprocess
import tempfile

BASE = 'dc95372b03e9b44cfeb1b8c88ec5f7f0f71152b7'
UPSTREAM = '966f96c2a607c0ca315269e4492efc1c5eee243f'
EXPECTED = {
 'scripts/full_recheck_inventory.py':'5ffe41adf123439d496cfb9efd76942bad8886e27cdf56fcbb4c148fa0aba598',
 'scripts/full_recheck_request.py':'76c1e7400251de6e0f452c04cbf60f9dba33db26a922c81bf8ad76ac6da41349',
 'scripts/v345_thematic_accelerator.py':'e7d2c0b26ba947d316a3f0289e142f30fe452a70acb7a66bd3b54ddb8dc9cac4',
 'scripts/v381_audit_scheduling.py':'5172e53b56169ed11a3257f8fc192c240011bdf6eb60204ce09c3d6df259913c',
 'scripts/v332_all_procurements_audit.py':'0fdf4f0546af3507636a48f5f43bce396ac08f7b70ff0404aafec2861be2d456',
 'scripts/v341_eis_facts.py':'8a762eea53add2807b3cc73be21f8a9f6458667196c1f64eed029e13373daf2b',
 'scripts/full_recheck_runtime.py':'838f9422ab419ac8f09e888c44e69ee78274ad2c698194fd1b17e828ba25e5dc',
 'tests/test_full_recheck_runtime.py':'374263fb0ae93d1c1e707f5f01ae452d445a8701a59e2bbd218c1f6ab253c7a5',
 'tests/test_fact_persistence_contention.py':'8e45b4250597aa0adc1096d84a8ca11890ff1330b5028f09521ebb1f509e1e25',
 'tests/test_recheck4007_safety.py':'9faff382db89298e2b87c77e162ccaa8b048da2ffddf297d22a97732dd50be37',
 'tests/test_dashboard_4007_request.py':'cd496e4f9581e9abdca43803a7ff50ddb37ce2c76d025b83d7fe6c576959c0fd',
}

def show(sha, path):
    r = subprocess.run(['git','show',sha+':'+path],capture_output=True)
    return r.stdout if r.returncode == 0 else None

subprocess.run(['git','fetch','--no-tags','origin',BASE,UPSTREAM],check=True)
resolved = {'scripts/full_recheck_inventory.py','scripts/full_recheck_request.py',
            'scripts/full_recheck_runtime.py','tests/test_full_recheck_runtime.py'}
paths = subprocess.check_output(['git','diff','--name-only',BASE,UPSTREAM],text=True).splitlines()
for name in paths:
    if not name.startswith(('scripts/','tests/')):
        continue
    path=Path(name); old=show(BASE,name); new=show(UPSTREAM,name)
    if new is None:
        raise RuntimeError('Unexpected upstream source deletion: '+name)
    ours=path.read_bytes() if path.exists() else None
    path.parent.mkdir(parents=True,exist_ok=True)
    if name in resolved or ours is None or ours == old:
        path.write_bytes(new)
    elif ours != new:
        if old is None:
            raise RuntimeError('Unexpected add/add conflict: '+name)
        with tempfile.TemporaryDirectory() as d:
            a,b,c=(Path(d)/v for v in ('ours','base','upstream'))
            a.write_bytes(ours); b.write_bytes(old); c.write_bytes(new)
            result=subprocess.run(['git','merge-file','-p',str(a),str(b),str(c)],capture_output=True)
            if result.returncode:
                raise RuntimeError('Unreviewed merge conflict: '+name)
            path.write_bytes(result.stdout)

p=Path('scripts/full_recheck_runtime.py');s=p.read_text()
s=s.replace('TARGET_GENERATION = 11','TARGET_GENERATION = 12')
old='''    state["inventory"] = collect(ns)
    state["inventory_at"] = time.monotonic()'''
new='''    # On the first explicit activation, capture immutable membership first.
    # Diagnostics may enumerate the same opaque source; do not do that twice.
    c = ns["conn"]()
    try:
        tables = {row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        exists = bool("full_recheck_requests" in tables and c.execute(
            "SELECT 1 FROM full_recheck_requests WHERE request_id=?", (epoch,)).fetchone())
    finally:
        c.close()
    state["inventory"] = ({"status": "deferred", "reason": "explicit_snapshot_priority",
                           "expected_requested_count": AUTHORIZED_REQUESTS.get(epoch)}
                          if epoch in AUTHORIZED_REQUESTS and not exists else collect(ns))
    state["inventory_at"] = time.monotonic()'''
assert old in s;s=s.replace(old,new,1)
s=s.replace('    state = ns.setdefault("_full_recheck", {})\n    # On the first',
            '    state = ns.setdefault("_full_recheck", {})\n    epoch = os.environ.get(REQUEST_ENV, "").strip()\n    # On the first',1)
p.write_text(s)
p=Path('scripts/v341_eis_facts.py');s=p.read_text()
s=s.replace('if row is not None and any(row[key] != value for key, value in snapshot.items()',
            'if current is not None and any(current[key] != value for key, value in snapshot.items()')
p.write_text(s)
p=Path('tests/test_full_recheck_runtime.py');s=p.read_text()
s=s.replace('''        self.assertEqual(runtime.TARGET_GENERATION, 11)
        await self._assert_generation_transition(10, 11)''','''        with patch.object(runtime, "TARGET_GENERATION", 11):
            await self._assert_generation_transition(10, 11)

    async def test_generation_twelve_verifies_clarification_publication_once(self):
        self.assertEqual(runtime.TARGET_GENERATION, 12)
        await self._assert_generation_transition(11, 12)''')
s=s.replace('''        self.assertEqual(runtime.TARGET_GENERATION, 11)
        await self._assert_pending_and_active_generation_transition(10, 11)''','''        with patch.object(runtime, "TARGET_GENERATION", 11):
            await self._assert_pending_and_active_generation_transition(10, 11)

    async def test_generation_twelve_keeps_pending_and_live_generation_eleven(self):
        self.assertEqual(runtime.TARGET_GENERATION, 12)
        await self._assert_pending_and_active_generation_transition(11, 12)''')
p.write_text(s)
p=Path('tests/test_dashboard_4007_request.py');s=p.read_text().replace('DASHBOARD_4007_EPOCH','DASHBOARD_EPOCH')
s=s.replace('        report = inventory.collect(ns, expected_count=4007)',
            '        with patch.dict(os.environ, {runtime.REQUEST_ENV: runtime.DASHBOARD_EPOCH}):\n            report = inventory.collect(ns)')
p.write_text(s)
p=Path('tests/test_fact_persistence_contention.py');s=p.read_text()
s=s.replace("c.execute('UPDATE purchases SET price=?', (str(len(count)),))",
            "c.execute('UPDATE purchases SET card_fields_json=?', (json.dumps({'concurrent_derived_cache': len(count)}),))")
s=s.replace("self.assertEqual(self.row()['price'], '4')",
            "self.assertEqual(json.loads(self.row()['card_fields_json']), {'concurrent_derived_cache': 4})")
p.write_text(s)
p=Path('tests/test_recheck4007_safety.py');p.write_text(p.read_text().replace('(3,4007,11)','(3,4007,12)'))
for name,digest in EXPECTED.items():
    actual=hashlib.sha256(Path(name).read_bytes()).hexdigest()
    if actual != digest:
        raise RuntimeError('Reviewed final-file checksum mismatch: '+name+' '+actual)
Path('/tmp/recheck105-files.json').write_text(json.dumps(sorted(EXPECTED)))
print('INTEGRATION_VERIFIED files='+str(len(EXPECTED))+' target_generation=12')
