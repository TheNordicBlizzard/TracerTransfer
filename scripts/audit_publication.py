"""Audit worktree, exact index blobs, all reachable history, ZIPs and frozen Python.

Private literal indicators belong in an external JSON list (--private-rules), not
in the public auditor itself. Findings never print matched secret values.
This complements, and does not replace, a full-history Gitleaks run.
"""
from __future__ import annotations
import argparse
import io
import hashlib
import json
import marshal
import os
from pathlib import Path
import re
import subprocess
import tempfile
import types
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = (
    (r'-----BEGIN (?:RSA |OPENSSH |EC |DSA |ENCRYPTED )?PRIVATE KEY-----', 'private-key'),
    (r'(?<![\d.])(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})(?![\d.])', 'private-ip'),
    (r'(?i)[a-z]:[\\/]Users[\\/](?!Public[\\/]|Default[\\/])[^\s\\/"<>:*?]+[\\/]', 'personal-home'),
    (r'(?i)\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b', 'github-token'),
    (r'(?i)\b(?:api[_-]?key|password|secret|access[_-]?token)\s*[=:]\s*["\'][^"\'\s]{16,}["\']', 'embedded-credential'),
    (r'(?i)(?:ssh-(?:rsa|ed25519)|ecdsa-sha2-nistp\d+)\s+[A-Za-z0-9+/]{50,}={0,3}', 'ssh-identity'),
    (r'(?i)\b(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}\b', 'hardware-mac'),
)


_BYTES_CACHE = {}
_FILE_CACHE = {}

def inspect_bytes(data, private_rules=None):
    cache_key = (hashlib.sha256(data).digest(), tuple(private_rules or []))
    if cache_key in _BYTES_CACHE:
        return list(_BYTES_CACHE[cache_key])
    texts = [data.decode('utf-8', errors='replace')]
    if b'\x00' in data:
        texts += [data.decode('utf-16-le', errors='ignore'), data.decode('utf-16-be', errors='ignore')]
    found = []
    for pattern, label in PATTERNS:
        if any(re.search(pattern, text) for text in texts):
            found.append(label)
    for index, literal in enumerate(private_rules or []):
        if not isinstance(literal, str) or not literal:
            raise ValueError('Private rules must be nonempty literal strings')
        if any(literal.casefold() in text.casefold() for text in texts):
            found.append(f'private-indicator-{index + 1}')
    result = sorted(set(found))
    if len(_BYTES_CACHE) >= 100000:
        _BYTES_CACHE.clear()
    _BYTES_CACHE[cache_key] = tuple(result)
    return result


def code_bytes(value):
    if isinstance(value, types.CodeType):
        # co_filename is build provenance, not a runtime string. Still audit it:
        # public binaries must not disclose a personal build path.
        yield value.co_filename.encode('utf-8', errors='surrogatepass')
        for item in value.co_consts:
            yield from code_bytes(item)
    elif isinstance(value, str):
        yield value.encode('utf-8', errors='surrogatepass')
    elif isinstance(value, bytes):
        yield value
    elif isinstance(value, (tuple, list, frozenset)):
        for item in value:
            yield from code_bytes(item)


def inspect_file(path, private_rules=None, require_pyinstaller=False, depth=0, innounp=None, scratch=None):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('Refusing linked audit input')
    data = path.read_bytes()
    cache_key = (hashlib.sha256(data).digest(), tuple(private_rules or []), require_pyinstaller)
    if cache_key in _FILE_CACHE:
        return list(_FILE_CACHE[cache_key])
    found = inspect_bytes(data, private_rules)
    if zipfile.is_zipfile(io.BytesIO(data)):
        if depth >= 5:
            raise ValueError('Nested ZIP depth exceeded')
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > 10000 or sum(e.file_size for e in entries) > 1024 ** 3:
                raise ValueError('Audit ZIP expansion limit exceeded')
            for entry in entries:
                if entry.is_dir():
                    continue
                if entry.file_size > 512 * 1024 ** 2 or entry.flag_bits & 1:
                    raise ValueError('Audit ZIP entry limit/encryption')
                payload = archive.read(entry)
                found += inspect_bytes(entry.filename.encode(), private_rules)
                found += inspect_bytes(payload, private_rules)
                if payload.startswith(b'MZ') or zipfile.is_zipfile(io.BytesIO(payload)):
                    with tempfile.TemporaryDirectory(prefix='publication-', dir=os.environ.get('TRACER_TEST_TMP') or os.environ.get('TMPDIR')) as folder:
                        nested = Path(folder) / 'nested.bin'; nested.write_bytes(payload)
                        found += inspect_file(nested, private_rules, depth=depth + 1, innounp=innounp, scratch=scratch)
    if data.startswith(b'MZ') and b'Inno Setup Setup Data' in data:
        innounp = innounp or os.environ.get('INNOUNP')
        scratch = scratch or os.environ.get('TRACER_TEST_TMP') or os.environ.get('TMPDIR')
        if not innounp or not scratch:
            raise ValueError('Installer audit requires --innounp and --scratch')
        if depth >= 5:
            raise ValueError('Nested installer depth exceeded')
        with tempfile.TemporaryDirectory(dir=scratch, prefix='publication-inno-') as folder:
            result = subprocess.run([str(innounp), '-x', '-b', '-y', '-m', '-d' + folder, str(path.resolve())], capture_output=True, timeout=120)
            if result.returncode:
                raise ValueError('Installer extraction failed')
            for extracted in Path(folder).rglob('*'):
                if extracted.is_file():
                    found += inspect_file(extracted, private_rules, depth=depth + 1, innounp=innounp, scratch=scratch)
    # Match the PyInstaller cookie rather than assume every PE is frozen Python.
    magic = b'MEI\014\013\012\013\016'
    if magic in data:
        from PyInstaller.archive.readers import CArchiveReader
        archive = CArchiveReader(str(path))
        for name, entry in archive.toc.items():
            found += inspect_bytes(name.encode(), private_rules)
            if entry[-1] == 'z':
                pyz = archive.open_embedded_archive(name)
                for module in pyz.toc:
                    found += inspect_bytes(module.encode(), private_rules)
                    for constant in code_bytes(pyz.extract(module)):
                        found += inspect_bytes(constant, private_rules)
            elif entry[-1] not in ('o', 'd'):
                payload = archive.extract(name)
                if payload:
                    found += inspect_bytes(payload, private_rules)
                    if zipfile.is_zipfile(io.BytesIO(payload)) or payload.startswith(b'MZ'):
                        if depth >= 5:
                            raise ValueError('Nested executable/archive depth exceeded')
                        with tempfile.TemporaryDirectory(prefix='publication-embedded-', dir=scratch or os.environ.get('TRACER_TEST_TMP') or os.environ.get('TMPDIR')) as folder:
                            nested = Path(folder) / 'payload.bin'; nested.write_bytes(payload)
                            found += inspect_file(nested, private_rules, depth=depth + 1, innounp=innounp, scratch=scratch)
                    if entry[-1] in ('s', 'm', 'M'):
                        for constant in code_bytes(marshal.loads(payload)):
                            found += inspect_bytes(constant, private_rules)
    elif require_pyinstaller:
        raise ValueError('Expected PyInstaller executable')
    result = sorted(set(found))
    _FILE_CACHE[cache_key] = tuple(result)
    return result


def git(root, *args):
    return subprocess.run(['git', *args], cwd=root, capture_output=True, check=True).stdout


def audit(root=ROOT, *, private_rules=None, history=True, artifacts=(), innounp=None, scratch=None):
    root = Path(root)
    findings = []; checked = {'worktree': 0, 'index': 0, 'history_blobs': 0, 'history_metadata': 0, 'artifacts': 0}
    def scan(label, data, kind):
        checked[kind] += 1
        rules = inspect_bytes(data, private_rules)
        # Frozen/archive Git blobs are inspected without executing any payload.
        if b'MEI\014\013\012\013\016' in data or b'Inno Setup Setup Data' in data or zipfile.is_zipfile(io.BytesIO(data)):
            with tempfile.TemporaryDirectory(prefix='publication-blob-', dir=scratch or os.environ.get('TRACER_TEST_TMP') or os.environ.get('TMPDIR')) as folder:
                candidate = Path(folder) / 'blob.bin'; candidate.write_bytes(data)
                rules += inspect_file(candidate, private_rules, innounp=innounp, scratch=scratch)
        if rules:
            findings.append({'target': label, 'rules': sorted(set(rules))})
    names = git(root, 'ls-files', '-z', '--cached', '--others', '--exclude-standard').split(b'\0')
    for raw in sorted(set(names)):
        if not raw:
            continue
        name = os.fsdecode(raw); path = root / name
        if path.is_file():
            scan('worktree:' + name, path.read_bytes(), 'worktree')
    # Read the exact staged bytes, not the (possibly different) worktree copy.
    for raw in git(root, 'ls-files', '-z', '--cached').split(b'\0'):
        if raw:
            name = os.fsdecode(raw)
            scan('index:' + name, git(root, 'show', ':' + name), 'index')
    if history:
        objects = git(root, 'rev-list', '--objects', '--all', '--reflog').splitlines()
        ids = sorted({row.split(b' ', 1)[0] for row in objects})
        for oid in ids:
            kind = git(root, 'cat-file', '-t', oid.decode()).strip()
            if kind in (b'blob', b'commit', b'tag'):
                key = 'history_blobs' if kind == b'blob' else 'history_metadata'
                scan('object:' + oid.decode(), git(root, 'cat-file', kind.decode(), oid.decode()), key)
    targets = list(artifacts) or [root / 'dist', root / 'release']
    for target in targets:
        target = Path(target)
        paths = sorted(target.rglob('*')) if target.is_dir() else [target]
        for path in paths:
            if not path.is_file():
                continue
            checked['artifacts'] += 1
            rules = inspect_file(path, private_rules, innounp=innounp, scratch=scratch)
            if rules:
                findings.append({'target': str(path), 'rules': sorted(set(rules))})
    return {'result': 'FAIL' if findings else 'PASS', 'checked': checked, 'findings': findings,
            'gitleaks_required_separately': True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--private-rules', type=Path)
    parser.add_argument('--artifacts', type=Path, action='append', default=[])
    parser.add_argument('--innounp', default=os.environ.get('INNOUNP'))
    parser.add_argument('--scratch', default=os.environ.get('TRACER_TEST_TMP') or os.environ.get('TMPDIR'))
    args = parser.parse_args(argv)
    if args.private_rules and args.private_rules.resolve().is_relative_to(args.root.resolve()):
        parser.error('Private rules must remain outside the repository')
    rules = json.loads(args.private_rules.read_text(encoding='utf-8')) if args.private_rules else None
    result = audit(args.root, private_rules=rules, artifacts=args.artifacts, innounp=args.innounp, scratch=args.scratch)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result['result'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
