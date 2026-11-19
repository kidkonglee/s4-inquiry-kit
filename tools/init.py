"""Create local secrets once, without printing them."""
from pathlib import Path
import secrets
root = Path(__file__).resolve().parents[1]
p = root / '.env'
if p.exists():
    raise SystemExit('.env 已存在，保留原有金鑰。')
p.write_text('ADMIN_TOKEN=' + secrets.token_urlsafe(32) + '\n'
             'ALLOWED_ORIGINS=http://127.0.0.1:8000,http://localhost:8000\n'
             'PORT=8000\nADMIN_PORT=8001\n', encoding='utf-8')
p.chmod(0o600)
print('已建立 .env。請在文字編輯器查看教師金鑰，勿上傳或分享。')
