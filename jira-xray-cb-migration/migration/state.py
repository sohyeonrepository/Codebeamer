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

    def save(self, directory, key, data):
        import hashlib
        dest = self.root / directory / (hashlib.sha256(str(key).encode()).hexdigest() + '.json')
        dest.parent.mkdir(parents=True, exist_ok=True)
        temporary = dest.with_suffix('.tmp')
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(dest)
        return dest
