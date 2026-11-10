"""Service/foreground entry point. Secrets only via private bootstrap JSON."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import threading

from delivery.agent import DeliveryAgent, activate, real_directory
from delivery.state import State, atomic_json
from configuration import get_default_output_dir


def verify_bootstrap_acl(path):
    if os.name != 'nt':
        raise OSError('Service bootstrap requires Windows private DACL')
    # No code/secret/password is put in arguments, environment, stdout or logs.
    script = r'''$ErrorActionPreference='Stop'
$p=$env:TT_BOOTSTRAP_DACL_PATH
$a=Get-Acl -LiteralPath $p
if(!$a.AreAccessRulesProtected){throw 'Inherited bootstrap ACL'}
$s=(New-Object Security.Principal.NTAccount('NT SERVICE','TracerTransferDelivery')).Translate([Security.Principal.SecurityIdentifier]).Value
$allowed=@('S-1-5-18','S-1-5-32-544',$s)
foreach($r in $a.Access){
 if($r.AccessControlType -eq 'Allow' -and $r.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -notin $allowed){throw 'Broad bootstrap ACL'}
}
'''
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                            env={**os.environ, 'TT_BOOTSTRAP_DACL_PATH': str(path)},
                            capture_output=True, check=False)
    if result.returncode:
        raise PermissionError('Bootstrap DACL is not private')


def main(argv=None):
    parser = argparse.ArgumentParser(description='TracerTransfer Windows delivery service')
    parser.add_argument('--data-dir', default=str(Path(os.environ.get('PROGRAMDATA', 'C:/ProgramData')) / 'TracerTransfer-Delivery' / 'data'))
    parser.add_argument('--output-dir', default=get_default_output_dir('C:/FreeForm/Tracer'))
    parser.add_argument('--poll-seconds', type=float, default=5)
    parser.add_argument('--once', action='store_true', help='One isolated polling cycle; no service installation')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--update', action='store_true', help='Internal SYSTEM task: detach protected self-copy')
    mode.add_argument('--apply-update', type=Path, metavar='REQUEST_DIR', help='Internal detached SYSTEM applier')
    parser.add_argument('--base-directory', type=Path)
    args = parser.parse_args(argv)
    if args.update or args.apply_update:
        if args.base_directory is None:
            parser.error('update modes require base-directory')
        from updates.windows import delivery_update_main
        return delivery_update_main(args.base_directory, args.apply_update)
    if args.base_directory:
        parser.error('base-directory requires an update mode')
    if not 0.1 <= args.poll_seconds <= 3600:
        parser.error('poll-seconds must be between 0.1 and 3600')
    directory = real_directory(args.data_dir)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    agent = None
    updates = None
    state = State(directory)  # Lock before first bootstrap/identity mutation.
    try:
        while not stop.is_set():
            try:
                bootstrap = directory / 'bootstrap.json'
                if bootstrap.exists():
                    real_directory(bootstrap.parent)
                    if bootstrap.is_symlink() or getattr(bootstrap.lstat(), 'st_file_attributes', 0) & 0x400:
                        raise ValueError('Bootstrap cannot be a link')
                    verify_bootstrap_acl(bootstrap)
                    activate(directory, json.loads(bootstrap.read_text(encoding='utf-8-sig')))
                    # Deletes the known plaintext bootstrap; no secure-erasure claim.
                    bootstrap.unlink()
                if agent is None:
                    agent = DeliveryAgent(directory, args.output_dir, state=state)
                    agent.stop_event = stop
                    from updates.runtime import installed_delivery, DeliveryUpdates
                    agent.auto_update = installed_delivery(directory, once=args.once)
                    if agent.auto_update:
                        updates = DeliveryUpdates(agent)
                        updates.start()
                agent.run_once()
                if updates:
                    try:
                        updates.apply_between_cycles()
                    except Exception:
                        pass
            except Exception:
                # Do not stringify network exceptions (may contain headers/bodies).
                atomic_json(directory / 'status.json', {'status': 'error', 'error': 'delivery_cycle_failed',
                            'at': datetime.now(timezone.utc).isoformat()})
                if args.once:
                    return 1
            if args.once:
                return 0
            stop.wait(args.poll_seconds)
    finally:
        stop.set()
        if updates:
            updates.close()
        if agent:
            agent.close()
        else:
            state.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
