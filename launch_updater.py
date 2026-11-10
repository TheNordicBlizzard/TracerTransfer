"""Absolute-import PyInstaller entry point for the detached Windows updater."""
from updates.windows import main

if __name__ == '__main__':
    raise SystemExit(main())
