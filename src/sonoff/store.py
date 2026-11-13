import json
import os
import sqlite3
import time
from pathlib import Path


class Store:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory = directory
        self.db = sqlite3.connect(directory / "controller.sqlite")
        os.chmod(directory / "controller.sqlite", 0o600)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, ts REAL, message TEXT)"
        )
        self.db.commit()

    def get(self, key: str):
        row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key: str, value):
        self.db.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, json.dumps(value)))
        self.db.commit()

    def event(self, message: str):
        self.db.execute("INSERT INTO events(ts,message) VALUES (?,?)", (time.time(), message))
        self.db.execute("DELETE FROM events WHERE id <= (SELECT MAX(id)-1000 FROM events)")
        self.db.commit()

    def events(self):
        return [
            dict(zip(("ts", "message"), row))
            for row in self.db.execute("SELECT ts,message FROM events ORDER BY id DESC LIMIT 50")
        ]

    def close(self):
        self.db.close()
