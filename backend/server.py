"""Separate public student API and loopback teacher service, served by Waitress."""
import json
import os
import secrets
import sqlite3
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
import threading
from flask import Flask, Response, request, send_file
from werkzeug.exceptions import HTTPException
from waitress import create_server
from security import RateLimiter, LimitReached
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
ADMIN_PORT = int(os.getenv('ADMIN_PORT', '8001'))
PUBLIC_ROUTES = {'/api/health': {'GET'}, '/api/join': {'POST'}, '/api/me': {'GET'}, '/api/events': {'POST'}}
ADMIN_ORIGINS = {'http://127.0.0.1:' + str(ADMIN_PORT), 'http://localhost:' + str(ADMIN_PORT)}
PUBLIC_HOSTS = {'127.0.0.1', 'localhost'} | {urlparse(x).hostname for x in ORIGINS if urlparse(x).hostname}
LIMITER = RateLimiter()
MAX_EVENTS = int(os.getenv('MAX_STUDENT_EVENTS', '5000'))
MAX_TRIALS = int(os.getenv('MAX_STUDENT_TRIALS', '300'))
MAX_BYTES = int(os.getenv('MAX_STUDENT_BYTES', '2000000'))
MAX_DB_BYTES = int(os.getenv('MAX_DB_BYTES', '536870912'))


def storage_available():
    return sum(p.stat().st_size for p in (DB, Path(str(DB) + '-wal'), Path(str(DB) + '-shm')) if p.exists()) < MAX_DB_BYTES


def json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('JSON 包含重複欄位')
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError('JSON 不接受非有限數值')

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
    os.umask(0o077)
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

class Service:
    def __init__(self, role):
        self.role = role
        self.headers = request.headers
        self.command = request.method
        self.path = request.path + ('?' + request.query_string.decode('utf-8') if request.query_string else '')

    def send(self, status=200, data=None, content_type='application/json; charset=utf-8'):
        raw = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
        return Response(raw, status=status, content_type=content_type)

    def dispatch(self):
        try:
            path = request.path
            if self.role == 'public':
                if path not in PUBLIC_ROUTES:
                    return self.send(404, {'error': '找不到頁面'})
                if self.command != 'OPTIONS' and self.command not in PUBLIC_ROUTES[path]:
                    return self.send(405, {'error': '不支援的請求方法'})
                origins = ORIGINS
            else:
                # This listener is never forwarded by ngrok. Ignore all X-Forwarded-* headers.
                if request.host.split(':')[0] not in {'127.0.0.1', 'localhost'}:
                    return self.send(400, {'error': '管理入口只限本機'})
                if self.headers.get('Forwarded') or any(k.lower().startswith('x-forwarded-') for k in self.headers.keys()):
                    return self.send(403, {'error': '管理入口不接受代理連線'})
                if not (path.startswith('/api/teacher/') or path in {'/', '/teacher', '/teacher.js', '/style.css'}):
                    return self.send(404, {'error': '找不到頁面'})
                origins = ADMIN_ORIGINS
            origin = self.headers.get('Origin')
            if origin and origin not in origins:
                return self.send(403, {'error': '來源未獲允許'})
            if not LIMITER.allow(('requests', self.role), 2400 if self.role == 'public' else 600):
                raise LimitReached('請求過於頻繁，請稍後再試')
            if self.command == 'OPTIONS':
                return self.send(204, b'')
            return self.route()
        except PermissionError:
            return self.send(401, {'error': '登入已失效或沒有權限，請重新登入'})
        except LimitReached as e:
            return self.send(429, {'error': str(e)})
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            return self.send(422, {'error': '資料格式、課堂狀態或版本不符，請檢查內容後重試'})
        except HTTPException:
            raise
        except Exception:
            print('伺服器錯誤；請檢查本地資料庫與設定。', file=sys.stderr)
            return self.send(500, {'error': '伺服器暫時無法處理；請保留裝置上的待傳紀錄'})

    def body(self):
        if request.mimetype != 'application/json':
            raise ValueError('只接受 JSON')
        obj = json.loads(request.get_data(), object_pairs_hook=json_object, parse_constant=reject_constant)
        if not isinstance(obj, dict):
            raise ValueError('請求須為 JSON 物件')
        return obj

    def token(self):
        return self.headers.get('Authorization', '').removeprefix('Bearer ')

    def teacher(self):
        if self.role != 'admin':
            raise PermissionError()
        candidate = self.token()
        if not candidate.isascii() or not ADMIN or not secrets.compare_digest(candidate, ADMIN):
            if not LIMITER.allow(('teacher_failures',), 20):
                raise LimitReached('登入失敗過多，請稍後再試')
            raise PermissionError()

    def student(self, c):
        r = c.execute('''SELECT s.*,c.lesson,c.closed FROM sessions t JOIN students s ON s.id=t.student_id
          JOIN classes c ON c.id=s.class_id WHERE t.token_hash=? AND t.expires>?''', (digest(self.token()), time.time())).fetchone()
        if not r: raise PermissionError()
        if not LIMITER.allow(('student', r['id']), 120):
            raise LimitReached('同步過於頻繁，請稍後重試')
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
            if not LIMITER.allow(('join',), 180):
                raise LimitReached('登入請求過多，請一分鐘後再試')
            if len(str(p.get('class_id', ''))) > 64 or len(str(p.get('code', ''))) > 128:
                raise ValueError('登入資料過長')
            with db() as c:
                s = c.execute('SELECT s.*,c.lesson,c.closed FROM students s JOIN classes c ON c.id=s.class_id WHERE s.class_id=? AND s.code_hash=?',
                              (str(p.get('class_id', '')).strip(), digest(str(p.get('code', '')).strip()))).fetchone()
                if not s: raise PermissionError()
                if s['closed']: raise ValueError('課堂已結束，請聯絡教師')
                token = secrets.token_urlsafe(32)
                c.execute('DELETE FROM sessions WHERE expires<?', (time.time(),))
                if not storage_available():
                    raise LimitReached('伺服器儲存空間已達安全上限，請聯絡教師')
                c.execute('DELETE FROM sessions WHERE student_id=? AND token_hash NOT IN (SELECT token_hash FROM sessions WHERE student_id=? ORDER BY expires DESC LIMIT 2)', (s['id'], s['id']))
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
                    counts = c.execute('SELECT COUNT(*),COALESCE(SUM(LENGTH(CAST(payload AS BLOB))),0),SUM(CASE WHEN type="trial" THEN 1 ELSE 0 END) FROM events WHERE student_id=?', (s['id'],)).fetchone()
                    event_count, byte_count, trial_count = counts[0], counts[1], counts[2] or 0
                    for e in clean:
                        old = c.execute('SELECT type,payload FROM events WHERE student_id=? AND event_id=?', (s['id'], e['id'])).fetchone()
                        if old:
                            if old['type'] != e['type'] or json.loads(old['payload']) != e['payload']:
                                raise ValueError('相同事件編號的內容不一致')
                            continue
                        if not storage_available():
                            raise LimitReached('伺服器儲存空間已達安全上限，請匯出備份並聯絡教師')
                        if e['type'] == 'submit' and not state(c, s['id'])['ready']:
                            raise ValueError('請完成所有文字欄位及至少三次實驗後再提交')
                        payload = json.dumps(e['payload'], ensure_ascii=False)
                        event_count += 1
                        byte_count += len(payload.encode('utf-8'))
                        trial_count += e['type'] == 'trial'
                        if event_count > MAX_EVENTS or byte_count > MAX_BYTES or trial_count > MAX_TRIALS:
                            raise LimitReached('本節課的紀錄已達安全容量上限；請匯出備份並聯絡教師')
                        c.execute('INSERT INTO events(student_id,event_id,type,payload,client_time,received_at) VALUES(?,?,?,?,?,?)',
                                  (s['id'], e['id'], e['type'], payload, e['client_time'], now()))
                    result = {'ack': [e['id'] for e in clean], 'state': state(c, s['id'])}
                    c.commit()  # Acknowledge only after a durable commit.
                    return self.send(data=result)
        if self.role == 'admin' and self.command == 'GET':
            local_files = {'/': ROOT / 'backend/teacher.html', '/teacher': ROOT / 'backend/teacher.html',
                           '/teacher.js': ROOT / 'backend/teacher.js', '/style.css': ROOT / 'docs/style.css'}
            if path in local_files:
                return send_file(local_files[path], conditional=False)
        return self.send(404, {'error': '找不到頁面'})

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
                if not storage_available() or c.execute('SELECT COUNT(*) FROM classes').fetchone()[0] >= 1000:
                    raise LimitReached('課堂或儲存容量已達安全上限，請先封存資料')
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
        return self.send(404, {'error': '找不到教師操作'})

def make_app(role):
    app = Flask('inquiry_' + role, static_folder=None)
    app.config.update(MAX_CONTENT_LENGTH=262144, MAX_FORM_PARTS=1,
                      TRUSTED_HOSTS=list(PUBLIC_HOSTS if role == 'public' else {'127.0.0.1', 'localhost'}))

    @app.route('/', defaults={'path': ''}, methods=['GET', 'POST', 'OPTIONS'])
    @app.route('/<path:path>', methods=['GET', 'POST', 'OPTIONS'])
    def endpoint(path):
        return Service(role).dispatch()

    @app.errorhandler(HTTPException)
    def http_error(error):
        return Service(role).send(error.code, {'error': '請求無效或超過安全限制'})

    @app.after_request
    def headers(response):
        origin = request.headers.get('Origin')
        allowed = ORIGINS if role == 'public' else ADMIN_ORIGINS
        if origin in allowed:
            response.headers['Access-Control-Allow-Origin'] = origin
            response.headers['Vary'] = 'Origin'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, ngrok-skip-browser-warning'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        response.headers['Access-Control-Max-Age'] = '600'
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
        response.headers['Content-Security-Policy'] = "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
        if role == 'public':
            response.headers['Strict-Transport-Security'] = 'max-age=31536000'
        if response.status_code == 429:
            response.headers['Retry-After'] = '60'
        return response
    return app


if __name__ == '__main__':
    if hasattr(os, 'geteuid') and os.geteuid() == 0:
        raise SystemExit('不可用 root 運行服務。')
    if len(ADMIN) < 24 or not ADMIN.isascii():
        raise SystemExit('先執行 tools/init.py，設定至少 24 字元的 ASCII ADMIN_TOKEN。')
    if PORT == ADMIN_PORT:
        raise SystemExit('學生與教師入口必須使用不同埠號。')
    init()
    options = dict(host='127.0.0.1', threads=8, connection_limit=64, backlog=64,
                   max_request_body_size=262144, max_request_header_size=16384,
                   channel_timeout=15, cleanup_interval=5, clear_untrusted_proxy_headers=True)
    public = create_server(make_app('public'), port=PORT, map={}, **options)
    admin = create_server(make_app('admin'), port=ADMIN_PORT, map={}, **options)
    threading.Thread(target=public.run, daemon=True).start()
    print('學生 API：http://127.0.0.1:' + str(PORT), flush=True)
    print('教師控制台（僅本機）：http://127.0.0.1:' + str(ADMIN_PORT) + '/teacher', flush=True)
    try:
        admin.run()
    except KeyboardInterrupt:
        pass
    finally:
        public.close()
        admin.close()
