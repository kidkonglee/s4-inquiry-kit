import concurrent.futures
import importlib.util
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from core import validate_lesson, clean_event, summarize

class SystemTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0)); cls.port = s.getsockname()[1]
        cls.admin = secrets.token_urlsafe(32)
        cls.env = dict(os.environ, ADMIN_TOKEN=cls.admin, PORT=str(cls.port),
                       CLASSROOM_DB=str(Path(cls.temp.name) / 'test.sqlite'),
                       ALLOWED_ORIGINS='https://school.example')
        cls.launch()

    @classmethod
    def launch(cls):
        cls.proc = subprocess.Popen([sys.executable, str(ROOT / 'backend/server.py')], env=cls.env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        for _ in range(80):
            try:
                cls.call('/api/health'); return
            except OSError: time.sleep(.05)
        raise RuntimeError('Server failed to start')

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate(); cls.proc.communicate(timeout=5); cls.temp.cleanup()

    @classmethod
    def call(cls, path, payload=None, token=None, origin=None):
        headers = {'Content-Type': 'application/json'}
        if token: headers['Authorization'] = 'Bearer ' + token
        if origin: headers['Origin'] = origin
        req = urllib.request.Request('http://127.0.0.1:' + str(cls.port) + path,
                                     data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                raw = r.read()
                return r.status, json.loads(raw) if 'application/json' in r.headers.get('Content-Type', '') else raw
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def new_class(self):
        status, c = self.call('/api/teacher/classes', {'name':'測試課堂','lesson_id':'newton-s4-v1','labels':['A01','A02']}, self.admin)
        self.assertEqual(status, 200)
        sessions = []
        for row in c['roster']:
            status, s = self.call('/api/join', {'class_id':c['id'], 'code':row['code']})
            self.assertEqual(status, 200); sessions.append(s)
        return c, sessions

    def event(self, kind, payload):
        return dict(id=uuid.uuid4().hex, type=kind, payload=payload, client_time='2026-11-18T00:00:00Z')

    def test_auth_and_private_files(self):
        _, sessions = self.new_class()
        self.assertEqual(self.call('/api/teacher/classes')[0], 401)
        self.assertEqual(self.call('/api/teacher/classes', token=sessions[0]['token'])[0], 401)
        self.assertEqual(self.call('/api/me', token='invalid')[0], 401)
        for path in ('/.env', '/private/classroom.sqlite', '/../backend/server.py', '/%2e%2e/.env'):
            self.assertEqual(self.call(path)[0], 404)

    def test_cors(self):
        self.assertEqual(self.call('/api/join', {'code':'bad'}, origin='https://evil.example')[0], 403)
        self.assertEqual(self.call('/api/health', origin='https://school.example')[0], 200)

    def test_validation_atomicity_and_recompute(self):
        _, sessions = self.new_class(); token = sessions[0]['token']
        good = self.event('answer', {'field':'prediction','value':'more force'})
        invalid = self.event('trial', {'force':999,'mass':0})
        self.assertEqual(self.call('/api/events', {'events':[good, invalid]}, token)[0], 422)
        self.assertEqual(self.call('/api/me', token=token)[1]['state']['answers']['prediction'], '')
        forged = self.event('trial', {'force':6,'mass':2,'acceleration':999})
        status, p = self.call('/api/events', {'events':[forged]}, token)
        self.assertEqual(status, 200); self.assertEqual(p['state']['trials'][0]['acceleration'], 3)
        halfstep = self.event('trial', {'force':4.1,'mass':2})
        self.assertEqual(self.call('/api/events', {'events':[halfstep]}, token)[0], 422)
        self.assertEqual(self.call('/api/events', {'events':[self.event('submit', {})]}, token)[0], 422)

    def test_retry_and_student_isolation(self):
        _, sessions = self.new_class(); a, b = sessions
        trial = self.event('trial', {'force':4,'mass':2})
        for _ in range(3): self.assertEqual(self.call('/api/events', {'events':[trial]}, a['token'])[0], 200)
        self.assertEqual(len(self.call('/api/me', token=a['token'])[1]['state']['trials']), 1)
        self.assertEqual(len(self.call('/api/me', token=b['token'])[1]['state']['trials']), 0)
        self.assertEqual(self.call('/api/teacher/detail?student_id='+a['student_id'], token=b['token'])[0], 401)
        trial['payload']['force'] = 5
        self.assertEqual(self.call('/api/events', {'events':[trial]}, a['token'])[0], 422)

    def test_workflow_scoring_revisions_and_close(self):
        c, sessions = self.new_class(); s = sessions[0]; token = s['token']
        batch = [self.event('answer', {'field':k,'value':'測試答案'}) for k in ('prediction','plan')]
        batch += [self.event('trial', {'force':f,'mass':2}) for f in (2,4,6)]
        batch += [self.event('answer', {'field':k,'value':'證據與推理待教師判讀'}) for k in ('analysis','claim','evidence','reasoning','reflection','transfer')]
        batch.append(self.event('submit', {}))
        status, p = self.call('/api/events', {'events':batch}, token)
        self.assertEqual(status, 200); state = p['state']
        self.assertEqual(state['completion'],100);self.assertTrue(state['submitted'])
        self.assertEqual(state['process_score'],40);self.assertIsNone(state['total'])
        grade = dict(student_id=s['student_id'], revision=state['revision'], scores=[10,11,12,13],comment='teacher')
        self.assertEqual(self.call('/api/teacher/grade', grade, self.admin)[0],200)
        self.assertEqual(self.call('/api/me',token=token)[1]['state']['total'],86)
        self.call('/api/events',{'events':[self.event('answer',{'field':'reasoning','value':'修改推理'})]},token)
        state = self.call('/api/me',token=token)[1]['state']
        self.assertFalse(state['submitted']);self.assertIsNone(state['total']);self.assertIsNotNone(state['previous_grade'])
        self.assertEqual(self.call('/api/teacher/grade',grade,self.admin)[0],422)
        self.call('/api/teacher/close',{'class_id':c['id'],'closed':True},self.admin)
        self.assertEqual(self.call('/api/events',{'events':[self.event('trial',{'force':4,'mass':2})]},token)[0],422)
        self.call('/api/teacher/close',{'class_id':c['id'],'closed':False},self.admin)
        self.assertEqual(self.call('/api/events',{'events':[self.event('submit',{})]},token)[0],200)

    def test_concurrent_uploads(self):
        _, sessions = self.new_class(); token = sessions[0]['token']
        batch = [self.event('trial', {'force':i%20,'mass':2}) for i in range(30)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            status = list(pool.map(lambda e:self.call('/api/events',{'events':[e]},token)[0],batch))
        self.assertEqual(status,[200]*30)
        self.assertEqual(len(self.call('/api/me',token=token)[1]['state']['trials']),30)

    def test_z_restart_persistence(self):
        _, sessions = self.new_class(); token = sessions[0]['token']
        self.call('/api/events',{'events':[self.event('trial',{'force':8,'mass':2})]},token)
        self.proc.terminate();self.proc.communicate(timeout=5);type(self).launch()
        self.assertEqual(self.call('/api/me',token=token)[1]['state']['trials'][0]['acceleration'],4)

class ModelTest(unittest.TestCase):
    def test_lesson_and_units(self):
        lesson = validate_lesson(json.loads((ROOT/'docs/lessons/newton.json').read_text()))
        e = clean_event(dict(id='test1234567',type='trial',payload=dict(force=10,mass=2)),lesson)
        self.assertEqual(e['payload']['acceleration'],5)
        self.assertEqual(e['payload']['displacement'],10)
        lesson['model']='unknown'
        with self.assertRaises(ValueError):validate_lesson(lesson)

    def test_no_credit_for_random_dragging_or_late_plan(self):
        def e(seq,kind,p):return dict(seq=seq,type=kind,payload=p,received_at='now')
        events=[e(1,'slider',dict(force=4,mass=2)),e(2,'trial',dict(force=4,mass=2)),
                e(3,'answer',dict(field='prediction',value='late')),e(4,'answer',dict(field='plan',value='late'))]
        self.assertEqual(summarize(events)['process_score'],0)
        self.assertTrue(summarize(events)['flags'])

if __name__ == '__main__':unittest.main(verbosity=2)
