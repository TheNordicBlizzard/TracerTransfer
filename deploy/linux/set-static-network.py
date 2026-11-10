"""Set this VM's Netplan address with a timed, guest-local rollback.

Run only as root inside the intended VM. Cancel rollback only after reconnecting
and verifying the new address from an independent connection. Never run on host.
"""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import uuid


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--address',required=True)
    parser.add_argument('--gateway',required=True)
    parser.add_argument('--dns',required=True,nargs='+')
    parser.add_argument('--interface',default='eth0')
    parser.add_argument('--expected-mac',required=True)
    args=parser.parse_args()
    if os.name!='posix' or os.geteuid()!=0:
        raise RuntimeError('Run as root inside the intended Linux VM')
    if not re.fullmatch('[A-Za-z0-9_-]{1,15}',args.interface):
        raise ValueError('Invalid interface')
    address=ipaddress.IPv4Interface(args.address)
    gateway=ipaddress.IPv4Address(args.gateway)
    if address.ip in (address.network.network_address,address.network.broadcast_address) or gateway not in address.network:
        raise ValueError('Invalid address/gateway relationship')
    for dns in args.dns:ipaddress.ip_address(dns)
    mac=(Path('/sys/class/net')/args.interface/'address').read_text().strip()
    if mac.lower()!=args.expected_mac.lower():
        raise RuntimeError('Interface MAC does not match intended VM')
    netplan=shutil.which('netplan')
    arping=shutil.which('arping')
    if not netplan or not arping:raise RuntimeError('netplan and arping required')
    if subprocess.run(['systemctl','is-active','--quiet','tracer-network-rollback.timer']).returncode==0:
        raise RuntimeError('A prior rollback timer is active')
    subprocess.run([arping,'-D','-I',args.interface,'-c','3','-w','5',str(address.ip)],check=True)
    target=Path('/etc/netplan/50-cloud-init.yaml')
    disable=Path('/etc/cloud/cloud.cfg.d/99-tracer-static-network.cfg')
    if target.is_symlink() or not target.is_file() or disable.exists():
        raise RuntimeError('Unexpected configuration state; refusing overwrite')
    backup=Path('/var/lib/tracer-network-backup')/uuid.uuid4().hex
    backup.mkdir(parents=True,mode=0o700)
    shutil.copy2(target,backup/'netplan.before')
    helper=backup/'rollback.py'
    helper.write_text('import shutil,subprocess\nfrom pathlib import Path\n'+
        'shutil.copy2('+repr(str(backup/'netplan.before'))+','+repr(str(target))+')\n'+
        'Path('+repr(str(disable))+').unlink(missing_ok=True)\n'+
        'subprocess.run(['+repr(netplan)+',"generate"],check=True)\n'+
        'subprocess.run(['+repr(netplan)+',"apply"],check=True)\n',encoding='utf-8')
    helper.chmod(0o700)
    body='network:\n  version: 2\n  renderer: networkd\n  ethernets:\n    '+args.interface+':\n      match:\n        macaddress: '+json.dumps(mac)+'\n      set-name: '+json.dumps(args.interface)+'\n      dhcp4: false\n      dhcp6: false\n      addresses: '+json.dumps([str(address)])+'\n      routes: '+json.dumps([{'to':'default','via':str(gateway)}])+'\n      nameservers:\n        addresses: '+json.dumps(args.dns)+'\n'
    try:
        target.write_text(body,encoding='utf-8');target.chmod(0o600)
        disable.write_text('network: {config: disabled}\n',encoding='utf-8');disable.chmod(0o600)
        subprocess.run([netplan,'generate'],check=True)
        subprocess.run(['systemd-run','--unit=tracer-network-rollback','--on-active=120s','/usr/bin/python3',str(helper)],check=True)
        subprocess.run(['systemd-run','--unit=tracer-network-apply','--on-active=3s',netplan,'apply'],check=True)
    except BaseException:
        shutil.copy2(backup/'netplan.before',target)
        disable.unlink(missing_ok=True)
        subprocess.run([netplan,'generate'],check=False)
        raise
    print(json.dumps({'scheduled':True,'address':str(address),'gateway':str(gateway),'backup':str(backup),'rollback_timer':'tracer-network-rollback.timer','instruction':'Reconnect to new IP; verify address/route/DNS; stop rollback timer after successful verification.'}))


if __name__=='__main__':main()
