#!/usr/bin/env bash
# Run from a downloaded, reviewed file. Do not put secrets in arguments.
set -euo pipefail
if [[ $(id -u) != 0 ]]; then
  echo 'Запустите установщик от root.' >&2
  exit 1
fi
command -v python3 >/dev/null || { echo 'Требуется Python 3 (пакет python3).' >&2; exit 1; }
command -v systemctl >/dev/null || { echo 'Требуется systemd.' >&2; exit 1; }
exec python3 - "$@" <<'PY'
import argparse
import ast
import getpass
import grp
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request

CA_SHA256 = 'd26d2d0231b7c39f92cc738512ba54103519e4405d68b5bd703e9788ca8ecf31'
CA_URL = 'https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt'
CONFIG = Path('/etc/eis223-worker/credentials.json')
WORKER = Path('/opt/eis223-worker/eis_vps_worker.py')
CA = Path('/etc/eis223-worker/eis-ca.crt')
UNIT = Path('/etc/systemd/system/eis223-worker.service')
ACCOUNT = 'eis223-worker'


def download(url, limit):
    with urllib.request.urlopen(url, timeout=30, context=ssl.create_default_context()) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise RuntimeError('Downloaded artifact exceeds size limit')
    return data


def certificate(data):
    try:
        der = ssl.PEM_cert_to_DER_cert(data.decode('ascii'))
    except (ValueError, UnicodeError):
        raise RuntimeError('Invalid CA certificate') from None
    if hashlib.sha256(der).hexdigest() != CA_SHA256:
        raise RuntimeError('CA fingerprint mismatch; installation stopped')
    return data


def atomic(path, data, mode, gid=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.install-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, mode)
        os.chown(temporary, 0, gid)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def run(*args):
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def read_secret(module, name, label, current=None):
    """Validate before HTTP and retry only the malformed field, without echoing it."""
    candidate = current
    while True:
        if candidate is None:
            candidate = getpass.getpass(label + ' (ввод скрыт): ')
        try:
            return module.validate_token(candidate, name)
        except module.WorkerError as error:
            if error.code == name + '_variable_name':
                hint = ('Скопируй её ЗНАЧЕНИЕ в Railway.' if name == 'site_token'
                        else 'Вставь само значение токена ЕИС.')
                print(label + ': введено название переменной. ' + hint)
            elif error.code == name + '_empty':
                print(label + ': пустой ввод. Вставь скопированное значение целиком.')
            elif error.code == name + '_invalid_characters':
                print(label + ': есть недопустимые символы — например, русские буквы, пробелы или невидимые знаки. Скопируй значение ключа заново.')
            else:
                raise
            candidate = None


def configure_intervals(config, interval_seconds=None, search_interval_seconds=None):
    # Preserve explicit settings and credentials while upgrading former defaults.
    if interval_seconds is not None:
        config.update(interval_seconds=interval_seconds, interval_custom=True)
    elif config.get('interval_custom') is not True:
        if config.get('interval_seconds', 300) in (30, 300):
            config.update(interval_seconds=30, interval_custom=False)
        else:
            config['interval_custom'] = True
    if search_interval_seconds is not None:
        config['search_interval_seconds'] = search_interval_seconds
    else:
        config.setdefault('search_interval_seconds', 60)
    for field in ('interval_seconds', 'search_interval_seconds'):
        value = config.get(field)
        if type(value) is not int or not 30 <= value <= 3600:
            raise RuntimeError(field + ' must be between 30 and 3600')


def setup():
    parser = argparse.ArgumentParser(description='Установка фонового загрузчика ЕИС. Секреты вводятся скрыто.')
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--source-dir', help='Локальная проверенная копия репозитория')
    source.add_argument('--revision', help='Полный неизменяемый Git commit SHA публичного репозитория')
    parser.add_argument('--worker-sha256', help='SHA256 проверенного eis_vps_worker.py; обязателен для --revision')
    parser.add_argument('--site-url', default='https://web-production-1c2ea.up.railway.app')
    parser.add_argument('--configure', action='store_true', help='Заново запросить оба токена')
    parser.add_argument('--interval-seconds', type=int, help='Интервал очереди от 30 до 3600 секунд; по умолчанию 30, заданный вручную интервал сохраняется')
    parser.add_argument('--search-interval-seconds', type=int, help='Интервал публичного поиска от 30 до 3600 секунд; по умолчанию 60, паузы ЕИС соблюдаются')
    parser.add_argument('--bootstrap-reg', default='32616233661')
    args = parser.parse_args()
    if args.interval_seconds is not None and not 30 <= args.interval_seconds <= 3600:
        parser.error('--interval-seconds must be between 30 and 3600')
    if args.search_interval_seconds is not None and not 30 <= args.search_interval_seconds <= 3600:
        parser.error('--search-interval-seconds must be between 30 and 3600')
    os.umask(0o077)
    if os.geteuid() != 0:
        raise RuntimeError('Root is required')
    if not re.fullmatch(r'[0-9]{11,20}', args.bootstrap_reg):
        raise RuntimeError('Invalid bootstrap registry number')
    if args.revision:
        if not re.fullmatch(r'[a-f0-9]{40}', args.revision) or not re.fullmatch(r'[a-f0-9]{64}', args.worker_sha256 or ''):
            raise RuntimeError('--revision requires a full commit SHA and --worker-sha256')
        url = 'https://raw.githubusercontent.com/Andrey12133311/eis223-auditor2/' + args.revision + '/scripts/eis_vps_worker.py'
        code = download(url, 512 * 1024)
    else:
        code = (Path(args.source_dir) / 'scripts/eis_vps_worker.py').read_bytes()
    if args.worker_sha256 and hashlib.sha256(code).hexdigest() != args.worker_sha256:
        raise RuntimeError('Worker SHA256 mismatch; installation stopped')
    ast.parse(code)
    existing_ca = Path('/root/eis-ca.crt')
    if CA.exists():
        ca_data = certificate(CA.read_bytes())
    elif existing_ca.exists():
        ca_data = certificate(existing_ca.read_bytes())
    else:
        ca_data = certificate(download(CA_URL, 1024 * 1024))
    if CONFIG.exists() and not args.configure:
        config = json.loads(CONFIG.read_text(encoding='utf-8'))
        print('Использую сохранённые реквизиты подключения.')
    else:
        print('Ключ сайта находится в Railway → проект eis223-auditor → web → Variables → EIS223_UPLOAD_TOKEN.')
        print('Скопируй значение переменной, которое открывается в Railway. Само название EIS223_UPLOAD_TOKEN вводить не нужно.')
        config = {'site_url': args.site_url.rstrip('/')}
    configure_intervals(config, args.interval_seconds, args.search_interval_seconds)
    config.update(ca_file=str(CA), timeout_seconds=30)
    # No service/configuration mutation before both authenticated connections pass.
    with tempfile.TemporaryDirectory(prefix='eis223-install-') as temporary:
        temp = Path(temporary)
        (temp / 'worker.py').write_bytes(code)
        (temp / 'ca.crt').write_bytes(ca_data)
        spec = importlib.util.spec_from_file_location('eis223_worker_preflight', temp / 'worker.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for name, label in (('eis_token', 'Токен ЕИС'),
                            ('site_token', 'Ключ сайта EIS223_UPLOAD_TOKEN')):
            config[name] = read_secret(module, name, label, config.get(name))
        check_config = {**config, 'ca_file': str(temp / 'ca.crt')}
        check_path = temp / 'credentials.json'
        check_path.write_text(json.dumps(check_config), encoding='utf-8')
        check_path.chmod(0o600)
        valid = module.load_config(check_path)
        http = module.Transport(valid)
        status = http.site('/api/eis-worker/status')
        if status.get('protocol_version') != 1 or status.get('feature') != 'eis-soap-vps-worker':
            raise RuntimeError('Site has not received the worker update; installation stopped')
        if type(status.get('max_original_bytes')) is not int or status['max_original_bytes'] < module.MAX_FILE:
            raise RuntimeError('Сначала обновите приёмник Railway: лимит оригинала ещё меньше лимита нового загрузчика')
        http.soap(args.bootstrap_reg)
        print('Ключ сайта, токен ЕИС и защищённые соединения проверены.')
        # Enqueue is idempotent and makes the first known purchase reviewable.
        http.site('/api/eis-worker/jobs', {'reg_number': args.bootstrap_reg, 'source': 'worker_bootstrap'})
    try:
        pwd.getpwnam(ACCOUNT)
    except KeyError:
        run('useradd', '--system', '--user-group', '--home-dir', '/var/lib/eis223-worker',
            '--no-create-home', '--shell', '/usr/sbin/nologin', ACCOUNT)
    gid = grp.getgrnam(ACCOUNT).gr_gid
    unit = '''[Unit]
Description=EIS 223-FZ outbound SOAP document worker
Wants=network-online.target
After=network-online.target
StartLimitIntervalSec=300
StartLimitBurst=5

[Service]
Type=simple
User=eis223-worker
Group=eis223-worker
UMask=0077
WorkingDirectory=/var/lib/eis223-worker
ExecStart=/usr/bin/python3 /opt/eis223-worker/eis_vps_worker.py --config /etc/eis223-worker/credentials.json --state-dir /var/lib/eis223-worker
Restart=on-failure
RestartSec=30
TimeoutStopSec=90
StateDirectory=eis223-worker
StateDirectoryMode=0700
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
CapabilityBoundingSet=
AmbientCapabilities=
MemoryMax=256M
TasksMax=32
ReadOnlyPaths=/etc/eis223-worker /opt/eis223-worker
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
'''.encode()
    prior = {}
    for path in (WORKER, CONFIG, CA, UNIT):
        if path.exists():
            st = path.stat()
            prior[path] = (path.read_bytes(), st.st_mode & 0o777, st.st_gid)
        else:
            prior[path] = None
    was_active = subprocess.run(['systemctl', 'is-active', '--quiet', 'eis223-worker.service']).returncode == 0
    try:
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(CONFIG.parent, 0o750)
        os.chown(CONFIG.parent, 0, gid)
        WORKER.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(WORKER.parent, 0o755)
        atomic(WORKER, code, 0o644)
        atomic(CA, ca_data, 0o644)
        atomic(CONFIG, json.dumps(config, indent=2).encode(), 0o640, gid)
        atomic(UNIT, unit, 0o644)
        run('systemctl', 'daemon-reload')
        run('systemctl', 'enable', 'eis223-worker.service')
        run('systemctl', 'restart', 'eis223-worker.service')
        run('systemctl', 'is-active', '--quiet', 'eis223-worker.service')
    except Exception:
        for path, previous in prior.items():
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                atomic(path, *previous)
        subprocess.run(['systemctl', 'daemon-reload'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if was_active:
            subprocess.run(['systemctl', 'restart', 'eis223-worker.service'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        raise
    print('Фоновый загрузчик запущен. Интервал очереди: ' + str(config['interval_seconds']) + ' секунд; до двух заданий и 48 оригиналов за цикл.')
    print('Публичный поиск: интервал ' + str(config['search_interval_seconds']) + ' секунд, до 50 закупок на страницу, последовательный просмотр до 10 страниц с возвратом к новым публикациям.')
    print('Поиск по ИНН и общий поиск делят этот интервал. При ограничении доступа сохраняется пауза ЕИС; закупки из переполненной очереди ожидают обработки.')
    print('Проверка состояния: systemctl status eis223-worker --no-pager')
    print('Последние события: journalctl -u eis223-worker -n 25 --no-pager')
    print('В журнале report_ack/download_complete=true подтверждает получение; результат проверки формируется на сайте отдельно.')

try:
    setup()
except KeyboardInterrupt:
    print('Установка отменена.', file=sys.stderr)
    sys.exit(130)
except Exception as error:
    # Approved fixed error messages may be shown; never display arbitrary exception URLs/credentials.
    if isinstance(error, RuntimeError):
        print('Установка остановлена: ' + str(error), file=sys.stderr)
    elif type(error).__name__ == 'WorkerError':
        print('Установка остановлена: ' + error.code, file=sys.stderr)
    else:
        print('Установка остановлена: ' + type(error).__name__, file=sys.stderr)
    sys.exit(1)
PY
