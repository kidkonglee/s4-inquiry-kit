from pathlib import Path
import sqlite3
from datetime import datetime
root = Path(__file__).resolve().parents[1]
source = root / 'private/classroom.sqlite'
if not source.exists():
    raise SystemExit('尚未有資料庫。')
dest = root / 'private/backups' / (datetime.now().strftime('%Y%m%d-%H%M%S') + '.sqlite')
dest.parent.mkdir(parents=True, exist_ok=True)
with sqlite3.connect(source) as a, sqlite3.connect(dest) as b:
    a.backup(b)
dest.chmod(0o600)
print('備份完成：' + str(dest))
