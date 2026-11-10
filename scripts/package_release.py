"""Compatibility entrypoint for the single allowlisted public client package."""
import argparse
import json
from pathlib import Path
import tomllib
try:
    from scripts.build_distribution_zips import ROOT, build_client_zip, calc_sha256
except ModuleNotFoundError:
    from build_distribution_zips import ROOT, build_client_zip, calc_sha256


def package_client(root=ROOT, version=None, output=None):
    root = Path(root)
    version = version or tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    path = build_client_zip(root, version, output)
    return {'package': str(path), 'bytes': path.stat().st_size, 'entries': 3,
            'sha256': calc_sha256(path), 'channel': 'pilot-unsigned'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    print(json.dumps(package_client(version=args.version, output=args.output)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
