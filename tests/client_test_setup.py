"""Exercise the technical Windows client package with a fresh test enrollment.

Requires TRACER_TEST_ENROLLMENT (secret file, never logged) and TRACER_TEST_TMP.
The caller provisions/revokes that exact server device outside this module.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

import httpx
from client.state import read_config, unprotect_token

ROOT = Path(__file__).resolve().parents[1]


def powershell(script, *args):
    return subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script), *map(str, args)], capture_output=True, timeout=45)


def main():
    enrollment = Path(os.environ['TRACER_TEST_ENROLLMENT'])
    parent = Path(os.environ['TRACER_TEST_TMP'])
    work = Path(tempfile.mkdtemp(prefix='client-package-', dir=parent))
    stage = work / 'package'
    stage.mkdir()
    for file in (ROOT / 'deploy' / 'client-test').iterdir():
        if file.is_file():
            shutil.copy2(file, stage / file.name)
    shutil.copy2(ROOT / 'dist' / 'TracerCliente.exe', stage / 'TracerCliente.exe')
    source = work / 'source'
    source.mkdir()
    data = work / 'data'
    credential = json.loads(enrollment.read_text(encoding='utf-8-sig'))
    configure = stage / 'Configurar-Cliente.ps1'
    result = powershell(configure, '-Cadastro', enrollment, '-PastaOrigem', source, '-PastaDados', data)
    assert result.returncode == 0, result.stdout.decode(errors='replace') + result.stderr.decode(errors='replace')
    cfg = read_config(data)
    assert cfg.device_id == credential['device_id']
    assert unprotect_token(cfg.protected_token) == credential['token']
    saved = (data / 'config.json').read_bytes()
    assert credential['token'].encode() not in saved
    result = powershell(configure, '-Cadastro', enrollment, '-PastaOrigem', source, '-PastaDados', data)
    assert result.returncode != 0 and (data / 'config.json').read_bytes() == saved
    invalid_count = 0
    for label, change in [('endpoint', {'server_url': 'http://untrusted.invalid'}),
                          ('schema', {'unexpected': 'field'}),
                          ('token', {'token': 'x' * 43})]:
        bad = work / (label + '.json')
        bad.write_text(json.dumps({**credential, **change}), encoding='utf-8')
        bad_data = work / ('invalid-' + label)
        result = powershell(configure, '-Cadastro', bad, '-PastaOrigem', source, '-PastaDados', bad_data)
        assert result.returncode != 0 and not bad_data.exists(), label
        invalid_count += 1
    missing = powershell(configure, '-Cadastro', enrollment, '-PastaOrigem', work/'missing', '-PastaDados', work/'missing-data')
    assert missing.returncode != 0 and not (work/'missing-data').exists()
    payloads = {'external-test.oma': b'SYNTHETIC OMA\r\n' + os.urandom(1024),
                'external-test.vca': b'SYNTHETIC VCA\r\n' + os.urandom(2048)}
    for name, payload in payloads.items():
        (source / name).write_bytes(payload)
    (source / 'ignored.txt').write_text('IGNORE', encoding='ascii')
    runner = stage / 'Iniciar-Teste.ps1'
    with (work / 'launcher.log').open('wb') as log:
        process = subprocess.Popen(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(runner), '-PastaDados', str(data)], stdout=log, stderr=subprocess.STDOUT)
        try:
            time.sleep(23)  # Three 5s polling windows plus EXE startup/stability.
            assert process.poll() is None, 'Test launcher exited early'
            with httpx.Client(base_url=cfg.server_url, timeout=30, trust_env=False) as http:
                headers = {'Authorization': 'Bearer ' + credential['token']}
                for name, payload in payloads.items():
                    digest = hashlib.sha256(payload).hexdigest()
                    response = http.put('/api/files/' + name, headers={**headers, 'X-Content-SHA256': digest}, content=payload)
                    assert response.status_code == 200 and response.json().get('duplicate') is True, 'EXE did not upload file before replay'
                    assert (source / name).read_bytes() == payload
        finally:
            # Kill only the process tree started by this test, never user apps.
            subprocess.run(['taskkill.exe', '/PID', str(process.pid), '/T', '/F'], capture_output=True, timeout=15)
            process.wait(timeout=15)
    report = {'endpoint': cfg.server_url, 'device_id': cfg.device_id, 'work_dir': str(work),
              'import_dpapi': 'PASS', 'overwrite_blocked': True, 'invalid_enrollments_rejected': invalid_count,
              'missing_source_rejected': True, 'launcher_and_exe': 'PASS', 'originals_preserved': True,
              'files': [{'name': name, 'size': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()} for name, payload in payloads.items()]}
    (parent / 'client-package-report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
