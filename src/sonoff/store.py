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
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS notifications "
            "(id TEXT PRIMARY KEY, message TEXT, next_due REAL, attempts INTEGER DEFAULT 0)"
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

    def enqueue_notification(self, event_id, message):
        if self.db.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] >= 200:
            self.event("Telegram: черга переповнена, повідомлення не додано")
            return
        self.db.execute(
            "INSERT OR IGNORE INTO notifications(id,message,next_due) VALUES (?,?,?)",
            (event_id, message, time.time()),
        )
        self.db.commit()

    def next_notification(self, now):
        row = self.db.execute(
            "SELECT id,message,attempts FROM notifications WHERE next_due <= ? "
            "ORDER BY next_due LIMIT 1",
            (now,),
        ).fetchone()
        return dict(zip(("id", "message", "attempts"), row)) if row else None

    def ack_notification(self, event_id):
        self.db.execute("DELETE FROM notifications WHERE id=?", (event_id,))
        self.db.commit()

    def retry_notification(self, event_id, next_due):
        self.db.execute(
            "UPDATE notifications SET attempts=attempts+1,next_due=? WHERE id=?",
            (next_due, event_id),
        )
        self.db.commit()

    def notification_counts(self):
        pending, failed = self.db.execute(
            "SELECT COUNT(CASE WHEN next_due IS NOT NULL THEN 1 END), "
            "COUNT(CASE WHEN next_due IS NULL THEN 1 END) FROM notifications"
        ).fetchone()
        return {"pending": pending, "failed": failed}

    def close(self):
        self.db.close()
