import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import server
from security import RateLimiter


class SecurityTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(server, 'DB', Path(self.temp.name) / 'test.sqlite')
        self.db_patch.start()
        self.admin_patch = patch.object(server, 'ADMIN', 'teacher-test-key-' + 'x' * 32)
        self.admin_patch.start()
        server.init()
        server.LIMITER.clear()
        self.public = server.make_app('public').test_client()
        self.admin = server.make_app('admin').test_client()
        self.auth = {'Authorization': 'Bearer ' + server.ADMIN}

    def tearDown(self):
        self.admin_patch.stop()
        self.db_patch.stop()
        self.temp.cleanup()

    def classroom(self):
        c = self.admin.post('/api/teacher/classes', json={'name': 'Security test', 'lesson_id': 'newton-s4-v1', 'labels': ['S01']}, headers=self.auth).get_json()
        joined = self.public.post('/api/join', json={'class_id': c['id'], 'code': c['roster'][0]['code']}).get_json()
        return c, joined

    def test_public_never_exposes_admin_even_with_real_key_and_spoofed_headers(self):
        for path in ('/', '/teacher', '/teacher.js', '/api/teacher/classes', '/api/teacher/export', '/.env', '/private/classroom.sqlite', '/%2e%2e/backend/server.py'):
            headers = dict(self.auth, **{'X-Forwarded-For': '127.0.0.1', 'X-Forwarded-Host': 'localhost', 'Forwarded': 'for=127.0.0.1'})
            self.assertEqual(self.public.get(path, headers=headers).status_code, 404, path)

    def test_admin_host_origin_and_auth(self):
        with self.admin.get('/teacher') as response:
            self.assertEqual(response.status_code, 200)
        self.assertEqual(self.admin.get('/teacher', headers={'Host': 'evil.example'}).status_code, 400)
        self.assertEqual(self.admin.get('/api/teacher/classes', headers={'Origin': 'https://evil.example', **self.auth}).status_code, 403)
        self.assertEqual(self.admin.get('/api/teacher/classes').status_code, 401)
        self.assertEqual(self.admin.get('/api/teacher/classes', headers=self.auth).status_code, 200)

    def test_strict_body_and_size(self):
        for data in ('{"code":"a","code":"b"}', '{"force":NaN}', '[]'):
            self.assertEqual(self.public.post('/api/join', data=data, content_type='application/json').status_code, 422)
        self.assertEqual(self.public.post('/api/join', data='{}', content_type='text/plain').status_code, 422)
        self.assertEqual(self.public.post('/api/join', data='x' * 262145, content_type='application/json').status_code, 413)

    def test_method_cors_and_security_headers(self):
        self.assertEqual(self.public.get('/api/join').status_code, 405)
        self.assertEqual(self.public.post('/api/health', json={}).status_code, 405)
        self.assertEqual(self.public.options('/api/teacher/classes').status_code, 404)
        self.assertEqual(self.public.post('/api/join', json={}, headers={'Origin': 'https://evil.example'}).status_code, 403)
        r = self.admin.get('/teacher')
        self.assertEqual(r.headers['X-Frame-Options'], 'DENY')
        self.assertIn("frame-ancestors 'none'", r.headers['Content-Security-Policy'])
        self.assertEqual(r.headers['Cache-Control'], 'no-store')
        r.close()

    def test_limits_do_not_leak_private_data(self):
        for _ in range(20):
            self.assertEqual(self.admin.get('/api/teacher/classes').status_code, 401)
        self.assertEqual(self.admin.get('/api/teacher/classes').status_code, 429)
        self.assertEqual(self.admin.get('/api/teacher/classes', headers=self.auth).status_code, 200)
        c, s = self.classroom()
        auth = {'Authorization': 'Bearer ' + s['token']}
        with patch.object(server, 'MAX_EVENTS', 1):
            first = {'id': 'security-test-first', 'type': 'trial', 'payload': {'force': 4, 'mass': 2}}
            second = dict(first, id='security-test-next')
            self.assertEqual(self.public.post('/api/events', json={'events': [first]}, headers=auth).status_code, 200)
            self.assertEqual(self.public.post('/api/events', json={'events': [first]}, headers=auth).status_code, 200)
            r = self.public.post('/api/events', json={'events': [second]}, headers=auth)
            self.assertEqual(r.status_code, 429)
            self.assertEqual(r.headers['Retry-After'], '60')
        with server.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM events').fetchone()[0], 1)

    def test_student_rate_and_bounded_memory(self):
        c, s = self.classroom()
        auth = {'Authorization': 'Bearer ' + s['token']}
        for _ in range(120):
            self.assertEqual(self.public.get('/api/me', headers=auth).status_code, 200)
        self.assertEqual(self.public.get('/api/me', headers=auth).status_code, 429)
        limiter = RateLimiter(max_keys=2)
        for i in range(5): limiter.allow(('key', i), 1)
        self.assertLessEqual(len(limiter.entries), 2)

    def test_sessions_are_bounded_and_storage_quota_is_checked(self):
        c, s = self.classroom()
        for _ in range(5):
            self.public.post('/api/join', json={'class_id': c['id'], 'code': c['roster'][0]['code']})
        with server.db() as db:
            self.assertLessEqual(db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0], 3)
        with patch.object(server, 'MAX_DB_BYTES', 1):
            r = self.public.post('/api/join', json={'class_id': c['id'], 'code': c['roster'][0]['code']})
            self.assertEqual(r.status_code, 429)

if __name__ == '__main__':
    unittest.main(verbosity=2)
