"""Local build receipts bind a frozen binary to the exact source snapshot compiled."""
import hashlib
import json
from pathlib import Path
import subprocess


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
                  'dirty': self.dirty, 'sources': self.sources, 'binary_sha256': digest(executable)}
        executable.with_suffix('.build.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
        return record
