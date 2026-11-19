"""Use the project runtime without changing system Python."""
from pathlib import Path
import os
import sys

root = Path(__file__).resolve().parents[1]
candidates = [root / '.runtime-venv/bin/python', root / '.venv/bin/python']
python = next((p for p in candidates if p.is_file()), None)
if python is None:
    raise SystemExit('先依 README 建立 .venv 並安裝 requirements.txt。')
os.chdir(root)
os.execv(str(python), [str(python), str(root / 'backend/server.py')])
