import hashlib
import json
import math
import re
from collections import defaultdict

FIELDS = ('prediction', 'plan', 'analysis', 'claim', 'evidence', 'reasoning', 'reflection', 'transfer')

def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()

def validate_lesson(lesson):
    for key in ('id', 'title', 'subject', 'version', 'textbook', 'reading', 'question',
                'predictionPrompt', 'planPrompt', 'analysisPrompt', 'reflectionPrompt',
                'transferPrompt', 'modelNote'):
        if not isinstance(lesson.get(key), str) or not lesson[key].strip():
            raise ValueError('學案缺少文字欄位：' + key)
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', lesson['id']):
        raise ValueError('學案 id 只可用英數字、底線與連字號')
    if lesson.get('model') != 'newton':
        raise ValueError('目前只支援 newton 模型；新模型須另寫仿真與驗證')
    if type(lesson.get('duration')) is not int or not 1 <= lesson['duration'] <= 180:
        raise ValueError('課時須為 1–180 分鐘的整數')
    if not isinstance(lesson.get('objectives'), list) or not lesson['objectives'] or any(not isinstance(x, str) or not x.strip() for x in lesson['objectives']):
        raise ValueError('請填寫至少一項學習目標')
    for name in ('force', 'mass'):
        r = lesson.get(name, {})
        for k in ('min', 'max', 'step', 'initial'):
            if type(r.get(k)) not in (int, float) or not math.isfinite(r[k]):
                raise ValueError('變量範圍必須是有限數字')
        if not (0 < r['step'] <= r['max'] - r['min'] and r['min'] <= r['initial'] <= r['max']):
            raise ValueError('變量範圍或初值不合法')
        if r['min'] < 0 or (name == 'mass' and r['min'] <= 0) or r['max'] > 1000:
            raise ValueError('模型只接受非負合力與正質量，最大 1000')
        for value in (r['initial'], r['max']):
            q = (value - r['min']) / r['step']
            if abs(q - round(q)) > 1e-6:
                raise ValueError('初值和最大值須對齊步長')
    return lesson

def clean_event(e, lesson):
    if not isinstance(e, dict) or not re.fullmatch(r'[a-zA-Z0-9_-]{10,80}', str(e.get('id', ''))):
        raise ValueError('事件編號無效')
    kind, p = e.get('type'), e.get('payload')
    if not isinstance(p, dict):
        raise ValueError('事件內容須為物件')
    if kind == 'answer':
        if p.get('field') not in FIELDS or not isinstance(p.get('value'), str) or len(p['value']) > 6000:
            raise ValueError('答案欄位或長度無效')
        p = {'field': p['field'], 'value': p['value']}
    elif kind == 'target':
        if p.get('value') not in ('force', 'mass'):
            raise ValueError('研究變量無效')
        p = {'value': p['value']}
    elif kind in ('trial', 'slider'):
        out = {}
        for name in ('force', 'mass'):
            v, r = p.get(name), lesson[name]
            if type(v) not in (int, float) or not math.isfinite(v) or not r['min'] <= v <= r['max']:
                raise ValueError('實驗數值超出範圍')
            q = (v - r['min']) / r['step']
            if abs(q - round(q)) > 1e-6:
                raise ValueError('實驗數值不符合步長')
            out[name] = v
        if kind == 'trial':
            out.update(acceleration=round(out['force'] / out['mass'], 6),
                       displacement=round(2 * out['force'] / out['mass'], 6))
        p = out
    elif kind == 'submit':
        p = {}
    else:
        raise ValueError('不支援的事件類型')
    return {'id': e['id'], 'type': kind, 'payload': p,
            'client_time': str(e.get('client_time', ''))[:50]}

def summarize(events, grade=None):
    answers = {k: '' for k in FIELDS}
    trials, pre, target = [], {}, 'force'
    target_before = None
    relevant_seq, submitted_seq = 0, 0
    for e in events:
        kind, p = e['type'], e['payload']
        if kind != 'slider':
            relevant_seq = e['seq']
        if kind == 'answer':
            answers[p['field']] = p['value']
            if not trials:
                pre[p['field']] = p['value'].strip()
        elif kind == 'target':
            target = p['value']
        elif kind == 'trial':
            if not trials:
                target_before = target
            trials.append(dict(p, seq=e['seq'], received_at=e['received_at']))
        elif kind == 'submit':
            submitted_seq = e['seq']
    research = target_before or target
    control = 'mass' if research == 'force' else 'force'
    groups = defaultdict(set)
    for t in trials:
        groups[t[control]].add(t[research])
    best = max(groups.values(), key=len, default=set())
    coverage = len(best) >= 3
    wide = coverage and max(best) - min(best) >= max(abs(max(best)), 1) / 2
    components = {'先提出預測': 10 if pre.get('prediction') else 0,
                  '先寫實驗計劃': 10 if pre.get('plan') else 0,
                  '同一控制量下至少三個研究值': 15 if coverage else 0,
                  '測試值有足夠跨度': 5 if wide else 0}
    # Presence is a completion signal, never a judgment of prose quality.
    stages = [bool(answers['prediction'].strip() and answers['plan'].strip()),
              len(trials) >= 3,
              all(answers[k].strip() for k in ('analysis', 'claim', 'evidence', 'reasoning')),
              all(answers[k].strip() for k in ('reflection', 'transfer'))]
    submitted = submitted_seq > 0 and submitted_seq == relevant_seq
    flags = []
    if trials and not pre.get('prediction'): flags.append('首輪實驗前沒有預測紀錄；請口頭追問')
    if trials and not pre.get('plan'): flags.append('首輪實驗前沒有計劃紀錄')
    if len(trials) >= 3 and not coverage: flags.append('尚無三個不同研究值的公平比較')
    if target_before and target_before != target: flags.append('研究變量曾改動；過程分按首輪研究變量計算，請教師覆核')
    current_grade = grade if grade and grade.get('revision') == relevant_seq else None
    return dict(answers=answers, target=target, trials=trials, components=components,
                process_score=sum(components.values()), completion=round(sum(stages + [submitted]) * 20),
                ready=all(stages), submitted=submitted, flags=flags, revision=relevant_seq,
                grade=current_grade, previous_grade=grade if grade and not current_grade else None,
                total=sum(components.values()) + sum(current_grade['scores']) if current_grade else None,
                last_seen=events[-1]['received_at'] if events else None)
