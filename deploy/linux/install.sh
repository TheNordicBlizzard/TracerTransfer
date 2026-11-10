#!/bin/sh
# Fresh Linux installation only. Existing data must be an approved staging copy.
set -eu
[ "$(id -u)" = 0 ] || { printf '%s\n' 'Run as root'; exit 1; }
source_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
[ -f "$source_root/server/app.py" ] && [ -f "$source_root/deploy/linux/requirements.txt" ] || { printf '%s\n' 'Incomplete deployment payload'; exit 1; }
: "${TRACER_WHEELHOUSE:?Provide a root-owned offline wheelhouse}"
export TRACER_WHEELHOUSE
# All inputs must be protected before the first mutation. Stage the audited
# payload under /root, not an operator-writable checkout or /tmp.
python3 - "$source_root" <<'PY'
import os, pathlib, stat, sys

def check(path):
    path = pathlib.Path(os.path.abspath(path))
    for entry in reversed((path, *path.parents)):
        info = entry.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise SystemExit('Unsafe installation input/ancestor: ' + str(entry))
source = pathlib.Path(sys.argv[1])
# A Linux requirements export predating signed updates omits cryptography.
# Detect that before creating an installation that cannot run its native updater.
import re
lock = (source / 'deploy/linux/requirements.txt').read_text(encoding='utf-8')
for dependency in ('cryptography', 'httpx'):
    if not re.search(r'^' + dependency + r'==[0-9]', lock, re.MULTILINE):
        raise SystemExit('Regenerate pinned Linux requirements with ' + dependency)
for path in (source, pathlib.Path(os.environ['TRACER_WHEELHOUSE'])):
    check(path)
    for folder, directories, files in os.walk(path, followlinks=False):
        for name in directories + files:
            check(pathlib.Path(folder) / name)
for parent in ('/opt', '/var/lib', '/var/backups', '/etc/systemd/system'):
    check(parent)
if os.path.lexists('/opt/tracer-transfer'):
    raise SystemExit('Installation exists; controlled bootstrap/update required')
for data in ('/var/lib/tracer-transfer', '/var/backups/tracer-transfer'):
    if pathlib.Path(data).is_symlink():
        raise SystemExit('Linked application data path forbidden')
PY
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
release=/opt/tracer-transfer/releases/0.1.3
install -d -m 0755 -o root -g root /opt/tracer-transfer/releases "$release/server" "$release/updates"
install -d -m 0700 -o root -g root /opt/tracer-transfer/cache
install -m 0644 "$source_root"/server/*.py "$release/server/"
install -m 0644 "$source_root"/updates/*.py "$release/updates/"
install -m 0644 "$source_root/configuration.py" "$release/configuration.py"
install -m 0644 "$source_root/deploy/linux/requirements.txt" "$release/requirements.txt"
python3 - "$release" <<'PY'
import hashlib, json, pathlib, sys
root = pathlib.Path(sys.argv[1])
(root / 'server-release.json').write_text(json.dumps({
    'schema': 1, 'version': '0.1.3', 'requirements_sha256': hashlib.sha256((root / 'requirements.txt').read_bytes()).hexdigest(),
    'database_compatible': True, 'migrations': 'none'}), encoding='utf-8')
PY
ln -s releases/0.1.3 /opt/tracer-transfer/current
ln -s current/server /opt/tracer-transfer/server
ln -s current/updates /opt/tracer-transfer/updates
ln -s current/configuration.py /opt/tracer-transfer/configuration.py
install -m 0644 "$source_root/deploy/linux/requirements.txt" /opt/tracer-transfer/requirements.txt
install -m 0644 "$source_root/deploy/linux/initialize.py" /opt/tracer-transfer/initialize.py
install -m 0644 "$source_root/deploy/linux/manage_admin.py" /opt/tracer-transfer/manage_admin.py
python3 -m venv /opt/tracer-transfer/venv
PIP_CONFIG_FILE=/dev/null /opt/tracer-transfer/venv/bin/python -m pip --isolated install --no-index --find-links "$TRACER_WHEELHOUSE" --only-binary=:all: --require-hashes --no-cache-dir -r /opt/tracer-transfer/requirements.txt
(cd "$release" && /opt/tracer-transfer/venv/bin/python -c 'import cryptography, httpx, server.update')
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
install -m 0644 "$source_root/deploy/linux/tracer-update.service" /etc/systemd/system/tracer-update.service
install -m 0644 "$source_root/deploy/linux/tracer-update.timer" /etc/systemd/system/tracer-update.timer
# Deliberately NOT enabled: require isolated Linux acceptance and operator opt-in.
systemd-analyze verify /etc/systemd/system/tracer-transfer.service /etc/systemd/system/tracer-maintenance.service /etc/systemd/system/tracer-maintenance.timer /etc/systemd/system/tracer-update.service /etc/systemd/system/tracer-update.timer
systemctl daemon-reload
systemctl enable --now tracer-transfer.service
systemctl enable --now tracer-maintenance.timer
printf '%s\n' 'Application enabled on loopback only. Verify actual HTTP health before claiming readiness.'
