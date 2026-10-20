#!/usr/bin/env bash
# Update an already installed VPS worker. No secrets in arguments or logs.
set -euo pipefail
[[ $(id -u) == 0 ]] || { echo 'Запустите этот файл от root.' >&2; exit 1; }
[[ -f /etc/eis223-worker/credentials.json ]] || {
  echo 'Сохранённые настройки загрузчика не найдены. Обновление остановлено.' >&2
  exit 1
}
command -v curl >/dev/null
command -v sha256sum >/dev/null
REVISION=5612b20231522bfbfe809c786a735153e31857f9
INSTALLER_SHA256=79129322133af9972d78133c9995ebb50a737526e30fa98e5ba1832b9dd98112
WORKER_SHA256=7e2a96568d44efdb25abe6c499fa2d940e0cedca09b5e1362fa49db7c71842e7
umask 077
WORKDIR=$(mktemp -d /tmp/eis223-upgrade.XXXXXXXX)
trap 'rm -f -- "$WORKDIR/install.sh"; rmdir -- "$WORKDIR" 2>/dev/null || true' EXIT
URL="https://raw.githubusercontent.com/Andrey12133311/eis223-auditor2/$REVISION/scripts/install_eis_vps_worker.sh"
echo 'Получаю проверенную версию 1.4.1. Токены и очередь будут сохранены.'
curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
  --connect-timeout 10 --max-time 90 "$URL" -o "$WORKDIR/install.sh"
printf '%s  %s\n' "$INSTALLER_SHA256" "$WORKDIR/install.sh" | sha256sum -c -
bash "$WORKDIR/install.sh" --revision "$REVISION" --worker-sha256 "$WORKER_SHA256"
echo 'Обновление выполнено. Версию VPS нужно подтвердить по heartbeat 1.4.1; это ещё не подтверждение загрузки недостающего оригинала.'
