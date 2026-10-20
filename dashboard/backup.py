"""Consistent SQLite online backup to stdout; no repository or credential files."""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

def main():
    source=Path(os.environ.get('DASHBOARD_DB','/data/leads.sqlite3'))
    if not source.is_file(): raise SystemExit('Database does not exist; start the dashboard first.')
    with tempfile.TemporaryDirectory() as temp:
        target=Path(temp)/'backup.sqlite3'
        with sqlite3.connect(source) as src,sqlite3.connect(target) as dst:
            src.backup(dst)
            if dst.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                raise SystemExit('Backup integrity check failed')
        with target.open('rb') as f:
            while chunk:=f.read(65536): sys.stdout.buffer.write(chunk)

if __name__=='__main__': main()
