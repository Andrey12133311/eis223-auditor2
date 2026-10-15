#!/usr/bin/env bash
# One-time installer for Ubuntu 24.04. Does not touch Railway data or app.
set -euo pipefail
if [[ $EUID != 0 ]]; then echo 'Run: sudo bash install.sh' >&2; exit 1; fi
ROOT=/opt/eis223-loader
ENV=/etc/eis223-loader.env
URL=https://raw.githubusercontent.com/Andrey12133311/eis223-auditor2/eis-vps-loader-20261015/vps-loader/collector.py
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3 python3-venv python3-pip ca-certificates curl
if ! id -u eis223 >/dev/null 2>&1; then
 useradd --system --create-home --home-dir /var/lib/eis223 --shell /usr/sbin/nologin eis223
fi
install -d -m 755 "$ROOT"
python3 -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/pip" install --no-cache-dir 'playwright>=1.55,<2'
export PLAYWRIGHT_BROWSERS_PATH="$ROOT/browsers"
"$ROOT/.venv/bin/python" -m playwright install --with-deps chromium
curl --retry 3 -fsSL "$URL" -o "$ROOT/collector.py"
"$ROOT/.venv/bin/python" -m py_compile "$ROOT/collector.py"
chown -R eis223:eis223 "$ROOT"
if [[ ! -f "$ENV" ]]; then
 echo 'Enter the EIS223_UPLOAD_TOKEN value from Railway → web → Variables.'
 echo 'Do not use the separate API token issued by EIS.'
 read -r -s -p 'Railway upload token: ' token
 echo
 if [[ ! "$token" =~ ^[A-Za-z0-9._:=-]{8,300}$ ]]; then
  echo 'No valid token entered; setup stopped' >&2
  exit 1
 fi
 printf 'EIS223_UPLOAD_TOKEN=%s\n' "$token" > "$ENV"
 unset token
 chmod 600 "$ENV"
 chown root:root "$ENV"
fi
cat >/etc/systemd/system/eis223-loader.service <<'UNIT'
[Unit]
Description=EIS 223-FZ original-document loader
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
User=eis223
Group=eis223
WorkingDirectory=/opt/eis223-loader
Environment=PYTHONUNBUFFERED=1
Environment=PLAYWRIGHT_BROWSERS_PATH=/opt/eis223-loader/browsers
EnvironmentFile=/etc/eis223-loader.env
ExecStart=/opt/eis223-loader/.venv/bin/python /opt/eis223-loader/collector.py
Restart=always
RestartSec=60
NoNewPrivileges=true
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now eis223-loader.service
echo 'Installed. Check: sudo systemctl status eis223-loader --no-pager'
echo 'Follow logs: sudo journalctl -u eis223-loader -f'
