"""Validate/init a private deployment. No passwords accepted on argv."""
import argparse
from pathlib import Path
from server.database import Database
from server.__main__ import consume_bootstrap


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--data-dir',required=True,type=Path)
    p.add_argument('--bootstrap-file',type=Path)
    args=p.parse_args()
    db=Database(args.data_dir)
    if args.bootstrap_file:
        consume_bootstrap(db,args.bootstrap_file)
    with db.connect() as c:
        if not c.execute("SELECT 1 FROM users WHERE role='admin' AND enabled=1").fetchone():
            raise RuntimeError('First administrator required via private bootstrap JSON; no default password')
    print('PASS: initialized database and enabled administrator verified')


if __name__=='__main__':main()
