"""Audit publication candidate files for leaked infrastructure or personal secrets."""
from __future__ import annotations
import subprocess
import sys
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

BANNED_PATTERNS = [
    (re.compile(r'goldmixlentes\.com\.br', re.IGNORECASE), 'Company domain'),
    (re.compile(r'172\.16\.9\.[0-9]+'), 'Lab internal IP address'),
    (re.compile(r'206\.42\.16\.215'), 'Lab public IP address'),
    (re.compile(r'(?:[0-9A-F]{2}[:-]){5}[0-9A-F]{2}', re.IGNORECASE), 'Hardware MAC address'),
    (re.compile(r'C:[\\/]Users[\\/]Hiro[\\/]', re.IGNORECASE), 'Personal home path'),
    (re.compile(r'BEGIN (?:RSA|OPENSSH|EC|DSA) PRIVATE KEY'), 'Private key material'),
]


def get_tracked_or_candidate_files() -> list[str]:
    tracked = subprocess.run(
        ['git', 'ls-files'],
        cwd=ROOT, capture_output=True, text=True, check=True
    )
    return sorted([line.strip() for line in tracked.stdout.splitlines() if line.strip()])


def audit() -> int:
    files = get_tracked_or_candidate_files()
    violations = []
    for rel_path in files:
        if rel_path in ('scripts/audit_publication.py', 'scripts\audit_publication.py'):
            continue
        full_path = ROOT / rel_path
        if not full_path.is_file():
            continue
        try:
            content = full_path.read_text(encoding='utf-8', errors='ignore')
        except Exception:
            continue
        for pattern, description in BANNED_PATTERNS:
            matches = pattern.findall(content)
            if matches:
                violations.append((rel_path, description, len(matches)))

    if violations:
        print('AUDIT FAILED: Banned data patterns found in repository files:')
        for path, desc, count in violations:
            print(f'  - {path}: {count} occurrences of {desc}')
        return 1

    print(f'AUDIT PASSED: {len(files)} files checked, zero banned patterns.')
    return 0


if __name__ == '__main__':
    sys.exit(audit())
