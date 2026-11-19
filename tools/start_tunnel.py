"""Forward only the student port, with the checked-in edge policy."""
from pathlib import Path
import json
import os
import shutil
import urllib.request
import urllib.error

root = Path(__file__).resolve().parents[1]
binary = shutil.which('ngrok')
if not binary:
    raise SystemExit('找不到 ngrok，請依官方指示安裝並登入。')
port = 8000
for line in (root / '.env').read_text().splitlines():
    if line.startswith('PORT='):
        port = int(line.split('=', 1)[1])
base = 'http://127.0.0.1:' + str(port)
try:
    with urllib.request.urlopen(base + '/api/health', timeout=3) as response:
        assert json.load(response)['ok']
    try:
        urllib.request.urlopen(base + '/api/teacher/classes', timeout=3)
        raise SystemExit('學生埠仍暴露教師 API，拒絕開啟隧道。')
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise SystemExit('學生埠隔離驗證失敗，拒絕開啟隧道。')
except OSError:
    raise SystemExit('請先啟動新版後端服務。')
os.chdir(root)
os.execv(binary, [binary, 'http', base, '--inspect=false', '--traffic-policy-file', str(root / 'ngrok-policy.json'), '--log=false'])
