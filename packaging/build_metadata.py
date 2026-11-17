"""Local build receipts bind a frozen binary to the exact source snapshot compiled."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sign_executable(root, executable, thumbprint):
    """Authenticode-sign through scripts/sign_windows.ps1; fail closed on any doubt."""
    if not re.fullmatch(r'[0-9A-F]{40}', thumbprint):
        raise ValueError('TRACER_SIGN_THUMBPRINT must be a 40-hex certificate thumbprint')
    script = Path(root) / 'scripts' / 'sign_windows.ps1'
    result = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command',
                             "& $args[0] -Thumbprint $args[1] -Path $args[2] | ConvertTo-Json -Compress",
                             str(script), thumbprint, str(Path(executable).resolve())],
                            capture_output=True, text=True, errors='replace')
    if result.returncode:
        raise ValueError('Authenticode signing failed: ' + (result.stderr or result.stdout)[-800:])
    line = [x for x in result.stdout.splitlines() if x.startswith('{')][-1]
    info = json.loads(line)
    return {'thumbprint': thumbprint, 'subject': info['Signer'], 'timestamp_authority': info['Timestamp']}


class BuildEvidence:
    def __init__(self, root, component):
        self.root = Path(root)
        self.component = component
        self.sources = self.snapshot()
        result = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=self.root, capture_output=True, text=True)
        self.commit = result.stdout.strip() if result.returncode == 0 else None
        result = subprocess.run(['git', 'status', '--porcelain', '--untracked-files=normal'], cwd=self.root, capture_output=True, text=True, check=True)
        self.dirty = bool(result.stdout.strip())

    def snapshot(self):
        paths = [self.root / f'launch_{self.component}.py', self.root / 'configuration.py',
                 self.root / 'pyproject.toml', self.root / 'uv.lock',
                 self.root / 'packaging' / f'{self.component}.spec', Path(__file__)]
        folders = (self.component, 'common', 'updates')
        if self.component == 'delivery':
            paths.append(self.root / 'deploy/windows-delivery/Update-Delivery.ps1')
        paths += [p for folder in folders for p in (self.root / folder).rglob('*.py')]
        return {p.relative_to(self.root).as_posix(): digest(p) for p in sorted(set(paths))
                if p.is_file() and p.is_relative_to(self.root)}

    def finish(self, executable):
        if self.snapshot() != self.sources:
            raise ValueError('Sources changed during build; discard the binary')
        executable = Path(executable)
        record = {'schema': 1, 'component': self.component, 'commit': self.commit,
                  'dirty': self.dirty, 'sources': self.sources}
        thumbprint = os.environ.get('TRACER_SIGN_THUMBPRINT', '').strip().upper()
        if thumbprint:
            # Sign BEFORE hashing so the receipt binds the exact bytes that ship.
            record['unsigned_sha256'] = digest(executable)
            record['authenticode'] = sign_executable(self.root, executable, thumbprint)
        record['binary_sha256'] = digest(executable)
        executable.with_suffix('.build.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
        return record
