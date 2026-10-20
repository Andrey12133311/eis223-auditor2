#!/usr/bin/env python3
"""Reproducible native Android build using official Android tools, Java and ECJ."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import zipfile

parser = argparse.ArgumentParser()
parser.add_argument('--toolchain', type=Path, required=True)
parser.add_argument('--signing', type=Path, required=True)
args = parser.parse_args()
source = Path(__file__).resolve().parent
build = source / 'build'
if build.exists(): shutil.rmtree(build)
build.mkdir()
tools = args.toolchain.resolve()
bt = next(p.parent for p in tools.rglob('aapt2') if p.is_file())
android = next(p for p in tools.rglob('android.jar') if p.is_file())
ecj = tools / 'ecj.jar'
sign = args.signing.resolve()
sign.mkdir(parents=True, exist_ok=True)
os.chmod(sign, 0o700)
password = sign / 'password.txt'
key = sign / 'release-signing.p12'

def run(*argv, capture=False):
    result = subprocess.run([str(a) for a in argv], check=True,
                            stdout=subprocess.PIPE if capture else None,
                            stderr=subprocess.STDOUT if capture else None, text=True)
    return result.stdout if capture else ''

if not key.exists():
    password.write_text(secrets.token_urlsafe(42))
    os.chmod(password, 0o600)
    run('keytool', '-genkeypair', '-keystore', key, '-storetype', 'PKCS12',
        '-storepass:file', password, '-keypass:file', password, '-alias', 'eisauditor',
        '-keyalg', 'RSA', '-keysize', '3072', '-validity', '10000',
        '-dname', 'CN=EIS223 Auditor Mobile, O=EIS223 Auditor, C=RU', '-noprompt')
    os.chmod(key, 0o600)

testclasses = build / 'tests'
testclasses.mkdir()
run('java', '-jar', ecj, '-source', '1.8', '-target', '1.8', '-d', testclasses,
    source / 'src/ru/eis223/auditor/UrlPolicy.java', source / 'tests/UrlPolicyTest.java')
test_result = run('java', '-cp', testclasses, 'ru.eis223.auditor.UrlPolicyTest', capture=True)
print(test_result, end='')

compiled = build / 'resources.zip'
gen = build / 'generated'
gen.mkdir()
unsigned = build / 'base.apk'
run(bt / 'aapt2', 'compile', '--dir', source / 'res', '-o', compiled)
run(bt / 'aapt2', 'link', '-o', unsigned, '--manifest', source / 'AndroidManifest.xml',
    '-I', android, '--java', gen, '--min-sdk-version', '26', '--target-sdk-version', '35', compiled)
classes = build / 'classes'
classes.mkdir()
java = list((source / 'src').rglob('*.java')) + list(gen.rglob('*.java'))
run('java', '-jar', ecj, '-source', '1.8', '-target', '1.8', '-bootclasspath', str(android) + os.pathsep + str(bt / 'core-lambda-stubs.jar'),
    '-d', classes, '-proc:none', *java)
dex = build / 'dex'
dex.mkdir()
run('java', '-cp', bt / 'lib/d8.jar', 'com.android.tools.r8.D8', '--release',
    '--min-api', '26', '--lib', android, '--output', dex, *classes.rglob('*.class'))
with zipfile.ZipFile(unsigned, 'a') as z:
    for p in dex.glob('*.dex'): z.write(p, p.name, compress_type=zipfile.ZIP_STORED)
aligned = build / 'aligned.apk'
run(bt / 'zipalign', '-f', '-p', '4', unsigned, aligned)
deliver = source.parent.parent / 'deliverables'
deliver.mkdir(exist_ok=True)
apk = deliver / 'EIS223-Auditor-Android-1.0.0.apk'
run(bt / 'apksigner', 'sign', '--ks', key, '--ks-key-alias', 'eisauditor',
    '--ks-pass', f'file:{password}', '--key-pass', f'file:{password}',
    '--v4-signing-enabled', 'false', '--out', apk, aligned)
verify = run(bt / 'apksigner', 'verify', '--verbose', '--print-certs', apk, capture=True)
run(bt / 'zipalign', '-c', '4', apk)
badging = run(bt / 'aapt', 'dump', 'badging', apk, capture=True)
with zipfile.ZipFile(apk) as z:
    assert z.testzip() is None
    assert {'AndroidManifest.xml', 'classes.dex', 'resources.arsc'}.issubset(z.namelist())
report = {
    'apk': apk.name, 'size_bytes': apk.stat().st_size,
    'sha256': hashlib.sha256(apk.read_bytes()).hexdigest(),
    'url_policy_tests': test_result.strip(), 'signature': verify.strip(),
    'package': badging.splitlines()[0],
    'tested_on_physical_android': False,
    'authenticated_server_flows_tested': False,
}
(deliver / 'android-build-verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(verify)
print(json.dumps({k: v for k, v in report.items() if k not in ['signature']}, ensure_ascii=False))
