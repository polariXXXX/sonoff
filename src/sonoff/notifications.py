import asyncio
import secrets
import time

import httpx


class Telegram:
    """A persistent outbox keeps Telegram I/O away from GPIO and socket control."""

    def __init__(self, store, token=None, chat_id=None):
        self.store, self.token, self.chat_id = store, token, chat_id
        self.enabled = bool(token and chat_id)
        self.client = httpx.AsyncClient(timeout=10)
        self.error = None

    def enqueue(self, message):
        if self.enabled:
            self.store.enqueue_notification(secrets.token_hex(16), message)

    def mains(self, present):
        if present is None:
            return
        previous = self.store.get("telegram_mains")
        self.store.put("telegram_mains", present)
        if present is False and previous is not False:
            self.enqueue("⚡ Мережа 220 В вимкнена.")

    def switched(self, name, action, source):
        verb = "увімкнена" if action == "on" else "вимкнена"
        origin = "вручну" if source == "manual" else "автоматично"
        self.enqueue(f"🔌 {name}: розетка {verb} ({origin}). Стан підтверджено eWeLink.")

    def status(self):
        return {"enabled": self.enabled, "error": self.error, **self.store.notification_counts()}

    async def deliver(self):
        if not self.enabled:
            return
        item = self.store.next_notification(time.time())
        if item is None:
            return
        delay = min(300, 5 * 2 ** min(item["attempts"], 6))
        permanent = False
        try:
            response = await self.client.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": item["message"]},
            )
            body = response.json()
            if response.status_code == 429:
                delay = min(86400, max(1, int(body.get("parameters", {}).get("retry_after", 60))))
            elif response.status_code in (400, 401, 403):
                permanent = True
            if response.is_success and body.get("ok") is True:
                self.store.ack_notification(item["id"])
                self.error = None
                return
        except Exception:
            # Telegram URLs contain the bot token: never log raw exceptions or responses.
            pass
        self.error = "Telegram: повідомлення не доставлено; перевірте налаштування та чергу"
        self.store.retry_notification(
            item["id"], None if permanent or item["attempts"] >= 9 else time.time() + delay
        )

    async def run(self):
        while True:
            await self.deliver()
            await asyncio.sleep(1)

    async def close(self):
        await self.client.aclose()
