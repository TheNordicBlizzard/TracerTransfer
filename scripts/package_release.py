"""Public client-only pilot packaging; administrative bundles stay separate."""
from pathlib import Path
import hashlib
import json
import re
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def package_client(root=ROOT):
    version=tomllib.loads((root/'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+',version):
        raise ValueError('Exact three-part version required')
    stem=f'TracerTransfer-Cliente-{version}-windows-x64-pilot-unsigned'
    installer=root/'dist/installers'/(stem+'.exe')
    record=json.loads(installer.with_suffix('.json').read_text(encoding='utf-8'))
    if record['version']!=version or record['channel']!='pilot-unsigned' or record['broad_distribution_approved'] is not False:
        raise ValueError('Pilot provenance mismatch')
    if installer.resolve()!=Path(record['installer']['path']).resolve() or digest(installer)!=record['installer']['sha256']:
        raise ValueError('Installer differs from verified build')
    if record['installer']['authenticode']!='NotSigned':
        raise ValueError('Use separately authorized signed-release workflow')
    if digest(root/'packaging/client.iss')!=record['recipe_sha256']:
        raise ValueError('Recipe changed since verified build')
    guide=root/'deploy/client-pilot/LEIA-ME.md'
    payloads={installer.name:installer,'LEIA-ME.md':guide}
    manifest={'version':version,'channel':'pilot-unsigned','broad_distribution_approved':False,
              'files':{name:{'sha256':digest(path),'bytes':path.stat().st_size} for name,path in payloads.items()}}
    release=root/'release';release.mkdir(exist_ok=True)
    path=release/f'TracerTransfer-Cliente-{version}-Piloto.zip'
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for name,source in payloads.items():archive.write(source,name)
        archive.writestr('SHA256.json',json.dumps(manifest,indent=2))
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None or set(archive.namelist())!=set(payloads)|{'SHA256.json'}:
            raise ValueError('ZIP integrity/whitelist failed')
        saved=json.loads(archive.read('SHA256.json'))
        for name,value in saved['files'].items():
            data=archive.read(name)
            if hashlib.sha256(data).hexdigest()!=value['sha256'] or len(data)!=value['bytes']:
                raise ValueError('Extracted payload mismatch')
    return {'package':str(path),'bytes':path.stat().st_size,'entries':len(payloads)+1,'sha256':digest(path),'channel':'pilot-unsigned'}


if __name__=='__main__':
    print(json.dumps(package_client(),ensure_ascii=False))
