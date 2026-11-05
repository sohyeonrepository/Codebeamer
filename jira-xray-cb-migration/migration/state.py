import json
import sqlite3
from pathlib import Path
from clients.http import ApiError


class State:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / 'state.sqlite3')
        self.db.execute('CREATE TABLE IF NOT EXISTS actions (key TEXT PRIMARY KEY, status TEXT NOT NULL, result TEXT)')
        self.db.commit()

    def get(self, key):
        row = self.db.execute('SELECT status,result FROM actions WHERE key=?', (key,)).fetchone()
        return (row[0], json.loads(row[1])) if row else None

    def mark(self, key, status, result):
        self.db.execute('INSERT OR REPLACE INTO actions VALUES (?,?,?)', (key, status, json.dumps(result)))
        self.db.commit()

    def once(self, key, action):
        previous = self.get(key)
        if previous:
            if previous[0] == 'done':
                return previous[1]
            raise ApiError('Uncertain prior write requires reconciliation: ' + key)
        self.mark(key, 'pending', None)
        result = action()
        self.mark(key, 'done', result)
        return result

    def field_phase(self, key, status, **evidence):
        allowed={'PLANNED','VALIDATED','CREATE_PENDING','CREATED','VALUE_MAPPING_PENDING',
                 'COMPLETED','FAILED','RECONCILIATION_REQUIRED'}
        if status not in allowed:raise ApiError('Unknown field phase')
        previous=self.get('field-phase:'+key)
        result=dict(previous[1]) if previous else {}
        history=list(result.get('history',[]))
        history.append({'status':status,**evidence})
        result.update(evidence,history=history)
        self.mark('field-phase:'+key,status,result)
        self.save('field-status',key,dict(result,status=status))
        return result

    def save(self, directory, key, data):
        import hashlib
        dest = self.root / directory / (hashlib.sha256(str(key).encode()).hexdigest() + '.json')
        dest.parent.mkdir(parents=True, exist_ok=True)
        temporary = dest.with_suffix('.tmp')
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        import os
        for attempt in range(5):
            try:
                os.replace(temporary, dest)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                __import__('time').sleep(0.2 * (attempt + 1))
        return dest

    def load(self, directory, key):
        import hashlib
        path = self.root / directory / (hashlib.sha256(str(key).encode()).hexdigest() + '.json')
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
