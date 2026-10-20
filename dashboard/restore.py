"""Explicit offline restore. Stop dashboard before running; rejects invalid DBs."""
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

def restore(source, target):
    source=Path(source).resolve();target=Path(target).resolve()
    if source==target or not source.is_file(): raise ValueError('Choose an existing, separate backup file')
    target.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as src:
        if src.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise ValueError('Invalid SQLite backup')
        tables={r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'leads','identities','projects','project_leads','jobs','geocache'}<=tables: raise ValueError('Not an Azar Dandeh backup')
        # Verify application columns before touching the target.
        src.execute('SELECT name,phone,email,notes,tags,status FROM leads LIMIT 1')
        src.execute('SELECT state,error,updated FROM jobs LIMIT 1')
        with tempfile.TemporaryDirectory(dir=target.parent) as temp:
            staged=Path(temp)/'restore.sqlite3'
            with sqlite3.connect(staged) as dst:
                src.backup(dst)
                dst.execute("UPDATE jobs SET state='failed',error='پس از بازیابی، اجرای ناتمام متوقف شد؛ برای جست‌وجوی دوباره پروژه جدید بسازید.' WHERE state IN ('queued','working')")
                dst.commit()
                dst.execute('PRAGMA wal_checkpoint(TRUNCATE)')
                dst.execute('PRAGMA journal_mode=DELETE')
            staged.chmod(0o600)
            for suffix in ('-wal','-shm'):
                Path(str(target)+suffix).unlink(missing_ok=True)
            os.replace(staged,target)
    print('Database restored. Start the dashboard.')

if __name__=='__main__':
    if len(sys.argv)!=2: raise SystemExit('Usage: python -m dashboard.restore /path/to/backup.sqlite3')
    restore(sys.argv[1],os.environ.get('DASHBOARD_DB','/data/leads.sqlite3'))
