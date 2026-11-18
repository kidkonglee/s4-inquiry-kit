"""Validate an AI/teacher-authored JSON before adding it to the lesson catalog."""
import json
import sys
from pathlib import Path
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'backend'))
from core import validate_lesson
if len(sys.argv) != 2:
    raise SystemExit('用法：python3 tools/add_lesson.py /path/to/new-lesson.json')
try:
    lesson = validate_lesson(json.loads(Path(sys.argv[1]).read_text(encoding='utf-8')))
    catalog_path = root / 'docs/lessons/catalog.json'
    catalog = json.loads(catalog_path.read_text())
    if any(x['id'] == lesson['id'] for x in catalog):
        raise ValueError('id 已存在，請用新的 id 發佈版本，保留歷史課堂')
    filename = lesson['id'] + '.json'
    dest = root / 'docs/lessons' / filename
    if dest.exists(): raise ValueError('目標檔案已存在')
    dest.write_text(json.dumps(lesson, ensure_ascii=False, indent=2) + '\n')
    catalog.append(dict(id=lesson['id'], file=filename, title=lesson['title']))
    catalog_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + '\n')
    print('已驗證並加入學案：' + lesson['id'] + '。請教師完成內容與模型覆核後才上課。')
except (ValueError, KeyError, OSError) as e:
    raise SystemExit('未加入：' + str(e))
