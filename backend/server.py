"""Small classroom pilot server. Python standard library only; run behind ngrok HTTPS."""
import csv
import io
import json
import mimetypes
import os
import secrets
import sqlite3
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from core import digest, validate_lesson, clean_event, summarize

ROOT = Path(__file__).resolve().parents[1]
for line in (ROOT / '.env').read_text().splitlines() if (ROOT / '.env').exists() else []:
    if '=' in line and not line.lstrip().startswith('#'):
        k, v = line.split('=', 1)
        os.environ.setdefault(k.strip(), v.strip())
ADMIN = os.getenv('ADMIN_TOKEN', '')
ORIGINS = set(filter(None, os.getenv('ALLOWED_ORIGINS', 'http://127.0.0.1:8000,http://localhost:8000').split(',')))
DB = Path(os.getenv('CLASSROOM_DB', str(ROOT / 'private/classroom.sqlite')))
PORT = int(os.getenv('PORT', '8000'))

def now():
    return datetime.now(timezone.utc).isoformat()

@contextmanager
def db():
    c = sqlite3.connect(DB, timeout=20)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    try:
        with c:
            yield c
    finally:
        c.close()

def init():
    DB.parent.mkdir(parents=True, exist_ok=True)
    DB.parent.chmod(0o700)
    with db() as c:
        c.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS classes(id TEXT PRIMARY KEY, name TEXT, lesson TEXT, closed INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS students(id TEXT PRIMARY KEY, class_id TEXT REFERENCES classes(id), label TEXT,
          code_hash TEXT, UNIQUE(class_id,label));
        CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, student_id TEXT REFERENCES students(id), expires REAL);
        CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT, student_id TEXT REFERENCES students(id),
          event_id TEXT, type TEXT, payload TEXT, client_time TEXT, received_at TEXT, UNIQUE(student_id,event_id));
        CREATE TABLE IF NOT EXISTS grades(student_id TEXT PRIMARY KEY REFERENCES students(id), data TEXT);
        ''')
    DB.chmod(0o600)

def events(c, sid):
    return [dict(r, payload=json.loads(r['payload'])) for r in c.execute('SELECT * FROM events WHERE student_id=? ORDER BY seq', (sid,))]

def state(c, sid):
    g = c.execute('SELECT data FROM grades WHERE student_id=?', (sid,)).fetchone()
    return summarize(events(c, sid), json.loads(g[0]) if g else None)

def catalog():
    items = json.loads((ROOT / 'docs/lessons/catalog.json').read_text())
    return [(item, validate_lesson(json.loads((ROOT / 'docs/lessons' / Path(item['file']).name).read_text()))) for item in items]

class Handler(BaseHTTPRequestHandler):
    server_version = 'InquiryClassroom'

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, fmt, *args):
        pass  # No request bodies, credentials, names, or URL queries in logs.

    def send(self, status=200, data=None, content_type='application/json; charset=utf-8'):
        raw = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(status)
        origin = self.headers.get('Origin')
        if origin in ORIGINS:
            self.send_header('Access-Control-Allow-Origin', origin)
            self.send_header('Vary', 'Origin')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization, ngrok-skip-browser-warning')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Max-Age', '600')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self):
        self.send(204, b'')

    def do_GET(self): self.dispatch()
    def do_POST(self): self.dispatch()

    def dispatch(self):
        try:
            if self.command == 'POST' and self.headers.get('Origin') and self.headers['Origin'] not in ORIGINS:
                return self.send(403, {'error': '前端來源未列入 ALLOWED_ORIGINS'})
            self.route()
        except PermissionError:
            self.send(401, {'error': '登入已失效或沒有權限，請重新登入'})
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
            self.send(422, {'error': str(e)[:200]})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            print('伺服器錯誤；請檢查本地資料庫與檔案設定。', file=sys.stderr)
            self.send(500, {'error': '伺服器暫時無法處理；資料仍保留在平板待傳佇列'})

    def body(self):
        n = int(self.headers.get('Content-Length', '0'))
        if n < 1 or n > 262144: raise ValueError('請求大小無效')
        obj = json.loads(self.rfile.read(n))
        if not isinstance(obj, dict): raise ValueError('請求須為 JSON 物件')
        return obj

    def token(self):
        return self.headers.get('Authorization', '').removeprefix('Bearer ')

    def teacher(self):
        if not ADMIN or not secrets.compare_digest(self.token(), ADMIN): raise PermissionError()

    def student(self, c):
        r = c.execute('''SELECT s.*,c.lesson,c.closed FROM sessions t JOIN students s ON s.id=t.student_id
          JOIN classes c ON c.id=s.class_id WHERE t.token_hash=? AND t.expires>?''', (digest(self.token()), time.time())).fetchone()
        if not r: raise PermissionError()
        return r

    def route(self):
        u = urlparse(self.path)
        path, q = u.path, parse_qs(u.query)
        if self.command == 'GET' and path == '/api/health':
            return self.send(data={'ok': True, 'server_time': now()})
        if path.startswith('/api/teacher/'):
            self.teacher()
            return self.teacher_route(path, q)
        if self.command == 'POST' and path == '/api/join':
            p = self.body()
            # Persistent student codes are high entropy; rate cap also limits online guesses.
            minute = int(time.time() // 60)
            if getattr(self.server, 'join_minute', None) != minute:
                self.server.join_minute, self.server.join_count = minute, 0
            self.server.join_count += 1
            if self.server.join_count > 240: return self.send(429, {'error': '登入請求過多，請一分鐘後再試'})
            with db() as c:
                s = c.execute('SELECT s.*,c.lesson,c.closed FROM students s JOIN classes c ON c.id=s.class_id WHERE s.class_id=? AND s.code_hash=?',
                              (str(p.get('class_id', '')).strip(), digest(str(p.get('code', '')).strip()))).fetchone()
                if not s: raise PermissionError()
                if s['closed']: raise ValueError('課堂已結束，請聯絡教師')
                token = secrets.token_urlsafe(32)
                c.execute('DELETE FROM sessions WHERE expires<?', (time.time(),))
                c.execute('INSERT INTO sessions VALUES(?,?,?)', (digest(token), s['id'], time.time() + 43200))
                return self.send(data={'token': token, 'student_id': s['id'], 'label': s['label'], 'class_id': s['class_id'],
                                       'lesson': json.loads(s['lesson']), 'state': state(c, s['id'])})
        if path in ('/api/me', '/api/events'):
            with db() as c:
                s = self.student(c)
                if self.command == 'GET' and path == '/api/me':
                    return self.send(data={'lesson': json.loads(s['lesson']), 'state': state(c, s['id']), 'closed': bool(s['closed'])})
                if self.command == 'POST' and path == '/api/events':
                    if s['closed']: raise ValueError('課堂已結束；請匯出備份並聯絡教師重開')
                    batch = self.body().get('events')
                    if not isinstance(batch, list) or not 1 <= len(batch) <= 40: raise ValueError('每批須為 1–40 個事件')
                    lesson = json.loads(s['lesson'])
                    clean = [clean_event(e, lesson) for e in batch]
                    c.execute('BEGIN IMMEDIATE')
                    for e in clean:
                        old = c.execute('SELECT type,payload FROM events WHERE student_id=? AND event_id=?', (s['id'], e['id'])).fetchone()
                        if old:
                            if old['type'] != e['type'] or json.loads(old['payload']) != e['payload']:
                                raise ValueError('相同事件編號的內容不一致')
                            continue
                        if e['type'] == 'submit' and not state(c, s['id'])['ready']:
                            raise ValueError('請完成所有文字欄位及至少三次實驗後再提交')
                        c.execute('INSERT INTO events(student_id,event_id,type,payload,client_time,received_at) VALUES(?,?,?,?,?,?)',
                                  (s['id'], e['id'], e['type'], json.dumps(e['payload'], ensure_ascii=False), e['client_time'], now()))
                    result = {'ack': [e['id'] for e in clean], 'state': state(c, s['id'])}
                    c.commit()  # Acknowledge only after a durable commit.
                    return self.send(data=result)
        if self.command == 'GET' and not path.startswith('/api/'):
            if path in ('/teacher', '/teacher.js'):
                f = ROOT / 'backend' / ('teacher.html' if path == '/teacher' else 'teacher.js')
            else:
                f = (ROOT / 'docs' / (path.lstrip('/') or 'index.html')).resolve()
                if not f.is_relative_to((ROOT / 'docs').resolve()): return self.send(404, {'error': '找不到頁面'})
            if f.is_file() and not f.name.startswith('.'):
                return self.send(data=f.read_bytes(), content_type=(mimetypes.guess_type(f.name)[0] or 'application/octet-stream') + '; charset=utf-8')
        self.send(404, {'error': '找不到頁面'})

    def teacher_route(self, path, q):
        if path == '/api/teacher/lessons' and self.command == 'GET':
            return self.send(data=[dict(id=l['id'], title=l['title']) for _, l in catalog()])
        with db() as c:
            if path == '/api/teacher/classes' and self.command == 'GET':
                return self.send(data=[dict(id=r['id'], name=r['name'], closed=bool(r['closed'])) for r in c.execute('SELECT * FROM classes ORDER BY rowid DESC')])
            if path == '/api/teacher/classes' and self.command == 'POST':
                p = self.body()
                lesson = next((l for _, l in catalog() if l['id'] == p.get('lesson_id')), None)
                labels = p.get('labels', [])
                if not lesson or not isinstance(labels, list) or not 1 <= len(labels) <= 60:
                    raise ValueError('請選有效學案，班別人數須為 1–60')
                if any(not isinstance(x, str) or not x.strip() or len(x) > 30 for x in labels) or len(set(labels)) != len(labels):
                    raise ValueError('學生代號須為不重複的 1–30 字文字')
                name = str(p.get('name', '')).strip()
                if not 1 <= len(name) <= 80: raise ValueError('請輸入課堂名稱（最多 80 字）')
                cid, roster = secrets.token_hex(4), []
                c.execute('INSERT INTO classes(id,name,lesson) VALUES(?,?,?)', (cid, name, json.dumps(lesson, ensure_ascii=False)))
                for label in labels:
                    code = secrets.token_urlsafe(9)
                    c.execute('INSERT INTO students VALUES(?,?,?,?)', (uuid.uuid4().hex, cid, label, digest(code)))
                    roster.append({'label': label, 'code': code})
                c.commit()
                return self.send(data={'id': cid, 'roster': roster})
            if path == '/api/teacher/close' and self.command == 'POST':
                p = self.body()
                if type(p.get('closed')) is not bool: raise ValueError('closed 必須為布林值')
                c.execute('UPDATE classes SET closed=? WHERE id=?', (int(p['closed']), p.get('class_id')))
                c.commit()
                return self.send(data={'ok': True})
            if path in ('/api/teacher/students', '/api/teacher/export') and self.command == 'GET':
                cid = q.get('class_id', [''])[0]
                rows = []
                for s in c.execute('SELECT * FROM students WHERE class_id=? ORDER BY label', (cid,)).fetchall():
                    rows.append({'id': s['id'], 'label': s['label'], 'state': state(c, s['id'])})
                return self.send(data=rows)
            if path == '/api/teacher/detail' and self.command == 'GET':
                sid = q.get('student_id', [''])[0]
                return self.send(data={'state': state(c, sid), 'events': events(c, sid)})
            if path == '/api/teacher/grade' and self.command == 'POST':
                p = self.body()
                scores = p.get('scores')
                if not isinstance(scores, list) or len(scores) != 4 or any(type(x) is not int or not 0 <= x <= 15 for x in scores):
                    raise ValueError('四項教師評分須為 0–15 的整數')
                sid = p.get('student_id')
                if not c.execute('SELECT 1 FROM students WHERE id=?', (sid,)).fetchone(): raise ValueError('學生不存在')
                c.execute('BEGIN IMMEDIATE')
                current = state(c, sid)
                if p.get('revision') != current['revision']: raise ValueError('學生有新紀錄，請重新開啟詳情後評分')
                comment = str(p.get('comment', ''))
                if len(comment) > 6000: raise ValueError('評語太長')
                grade = dict(scores=scores, comment=comment, revision=current['revision'], graded_at=now())
                c.execute('INSERT OR REPLACE INTO grades VALUES(?,?)', (sid, json.dumps(grade, ensure_ascii=False)))
                c.commit()
                return self.send(data={'ok': True})
        self.send(404, {'error': '找不到教師操作'})

if __name__ == '__main__':
    if len(ADMIN) < 24: raise SystemExit('先執行 python3 tools/init.py，或設定至少 24 字元的 ADMIN_TOKEN。')
    init()
    print('學生端：http://127.0.0.1:' + str(PORT), flush=True)
    print('教師端：http://127.0.0.1:' + str(PORT) + '/teacher', flush=True)
    server = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    server.daemon_threads = True
    server.request_queue_size = 128
    try: server.serve_forever()
    except KeyboardInterrupt: server.server_close()
