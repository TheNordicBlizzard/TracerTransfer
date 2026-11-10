"""Current queued smoke: real client/delivery EXEs and a real Python backend.

No Windows receiver/service is installed; tests.integration_transfer retains
DIRECT compatibility separately. Override both EXE paths explicitly if needed.
"""
import argparse
import json
from pathlib import Path
import tomllib
from tests.integration_queued import run

ROOT=Path(__file__).resolve().parents[1]


def main(argv=None):
    version=tomllib.loads((ROOT/'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client-exe',type=Path,default=ROOT/'dist'/version/'TracerCliente.exe')
    parser.add_argument('--delivery-exe',type=Path,default=ROOT/'dist'/version/'TracerEntregador.exe')
    args=parser.parse_args(argv)
    for exe in (args.client_exe,args.delivery_exe):
        if not exe.is_file():raise FileNotFoundError(exe)
    result=run(client_exe=args.client_exe,delivery_exe=args.delivery_exe)
    print(json.dumps(result,sort_keys=True))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
