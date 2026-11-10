#!/bin/sh
# Fresh Linux installation only. Existing data must be an approved staging copy.
set -eu
[ "$(id -u)" = 0 ] || { printf '%s\n' 'Run as root'; exit 1; }
source_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
[ -f "$source_root/server/app.py" ] && [ -f "$source_root/deploy/linux/requirements.txt" ] || { printf '%s\n' 'Incomplete deployment payload'; exit 1; }
[ ! -e /opt/tracer-transfer ] || { printf '%s\n' 'Installation exists; use controlled update, not overwrite'; exit 1; }
if [ ! -f /var/lib/tracer-transfer/tracer.sqlite3 ] && [ -z "${1:-}" ]; then
    printf '%s\n' 'Provide initialized staging database or private bootstrap file before installing'
    exit 1
fi
if ! id tracer >/dev/null 2>&1; then
    useradd --system --user-group --home-dir /var/lib/tracer-transfer --shell /usr/sbin/nologin tracer
fi
install -d -m 0755 -o root -g root /opt/tracer-transfer
install -d -m 0700 -o tracer -g tracer /var/lib/tracer-transfer /var/backups/tracer-transfer
cp -R "$source_root/server" /opt/tracer-transfer/server
install -m 0644 "$source_root/deploy/linux/requirements.txt" /opt/tracer-transfer/requirements.txt
install -m 0644 "$source_root/deploy/linux/initialize.py" /opt/tracer-transfer/initialize.py
install -m 0644 "$source_root/deploy/linux/manage_admin.py" /opt/tracer-transfer/manage_admin.py
python3 -m venv /opt/tracer-transfer/venv
/opt/tracer-transfer/venv/bin/python -m pip install --disable-pip-version-check --only-binary=:all: --require-hashes -r /opt/tracer-transfer/requirements.txt
chown -R root:root /opt/tracer-transfer
chmod -R go-w /opt/tracer-transfer
chown -R tracer:tracer /var/lib/tracer-transfer
cd /opt/tracer-transfer
if [ -n "${1:-}" ]; then
    install -m 0600 -o tracer -g tracer "$1" /var/lib/tracer-transfer/bootstrap.json
    runuser -u tracer -- /opt/tracer-transfer/venv/bin/python /opt/tracer-transfer/initialize.py --data-dir /var/lib/tracer-transfer --bootstrap-file /var/lib/tracer-transfer/bootstrap.json
else
    runuser -u tracer -- /opt/tracer-transfer/venv/bin/python /opt/tracer-transfer/initialize.py --data-dir /var/lib/tracer-transfer
fi
install -m 0644 "$source_root/deploy/linux/tracer-transfer.service" /etc/systemd/system/tracer-transfer.service
install -m 0644 "$source_root/deploy/linux/tracer-maintenance.service" /etc/systemd/system/tracer-maintenance.service
install -m 0644 "$source_root/deploy/linux/tracer-maintenance.timer" /etc/systemd/system/tracer-maintenance.timer
systemd-analyze verify /etc/systemd/system/tracer-transfer.service /etc/systemd/system/tracer-maintenance.service /etc/systemd/system/tracer-maintenance.timer
systemctl daemon-reload
systemctl enable --now tracer-transfer.service
systemctl enable --now tracer-maintenance.timer
printf '%s\n' 'Application enabled on loopback only. Verify actual HTTP health before claiming readiness.'
