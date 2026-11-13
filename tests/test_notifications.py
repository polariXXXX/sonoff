import httpx

from sonoff.cloud import MockCloud
from sonoff.config import Config, SensorConfig, Socket
from sonoff.engine import Engine
from sonoff.notifications import Telegram
from sonoff.store import Store


async def test_notifications_only_after_confirmed_auto_and_manual_switches(tmp_path):
    store = Store(tmp_path)
    notifier = Telegram(store, "test-token", "123")
    cloud = MockCloud()
    config = Config(
        sensor=SensorConfig(debounce_seconds=0), sockets=[Socket(device_id="boiler", name="Бойлер")]
    )
    engine = Engine(config, cloud, store, notifier=notifier)
    engine.sample(False)
    await engine.tick()
    engine.sample(True)
    engine.manual("boiler", "on")
    await engine.tick()
    messages = [row[0] for row in store.db.execute("SELECT message FROM notifications")]
    assert len(messages) == 3
    assert "Мережа 220 В вимкнена" in messages[0]
    assert "вимкнена (автоматично)" in messages[1]
    assert "увімкнена (вручну)" in messages[2]
    await notifier.close()
    store.close()


async def test_failed_device_command_does_not_report_success(tmp_path):
    store = Store(tmp_path)
    notifier = Telegram(store, "test-token", "123")

    class OfflineCloud(MockCloud):
        async def switch(self, *args, **kwargs):
            raise OSError("offline")

    engine = Engine(
        Config(sockets=[Socket(device_id="boiler", name="Бойлер")]),
        OfflineCloud(),
        store,
        notifier=notifier,
    )
    engine.sample(False)
    await engine.tick()
    assert store.notification_counts()["pending"] == 1  # Outage only, not a successful OFF.
    await notifier.close()
    store.close()


async def test_outage_deduplicated_across_service_restart(tmp_path):
    store = Store(tmp_path)
    notifier = Telegram(store, "test-token", "123")
    notifier.mains(False)
    notifier.mains(False)
    await notifier.close()
    restarted = Telegram(store, "test-token", "123")
    restarted.mains(False)
    assert store.notification_counts()["pending"] == 1
    restarted.mains(True)
    restarted.mains(False)
    assert store.notification_counts()["pending"] == 2
    await restarted.close()
    store.close()


async def test_successful_telegram_delivery_removes_persisted_message(tmp_path):
    store = Store(tmp_path)
    notifier = Telegram(store, "test-token", "123")
    notifier.enqueue("Test message")
    await notifier.client.aclose()
    notifier.client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": True}))
    )
    await notifier.deliver()
    assert store.notification_counts() == {"pending": 0, "failed": 0}
    await notifier.close()
    store.close()


async def test_permanent_telegram_error_is_visible_without_leaking_token(tmp_path):
    store = Store(tmp_path)
    notifier = Telegram(store, "private-token", "123")
    notifier.enqueue("Message")
    await notifier.client.aclose()
    notifier.client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(403, json={"ok": False}))
    )
    await notifier.deliver()
    assert notifier.status()["failed"] == 1
    assert "private-token" not in str(notifier.status())
    await notifier.close()
    store.close()


async def test_pending_notification_survives_restart(tmp_path):
    store = Store(tmp_path)
    notifier = Telegram(store, "test-token", "123")
    notifier.enqueue("Persisted message")
    await notifier.close()
    store.close()
    reopened = Store(tmp_path)
    assert reopened.notification_counts()["pending"] == 1
    reopened.close()


async def test_disabled_notifications_do_not_accumulate_messages(tmp_path):
    store = Store(tmp_path)
    notifier = Telegram(store)
    notifier.mains(False)
    notifier.switched("Бойлер", "on", "manual")
    assert notifier.status() == {"enabled": False, "error": None, "pending": 0, "failed": 0}
    await notifier.close()
    store.close()


async def test_telegram_rate_limit_respects_retry_after(tmp_path, monkeypatch):
    monkeypatch.setattr("sonoff.notifications.time.time", lambda: 1000)
    store = Store(tmp_path)
    notifier = Telegram(store, "test-token", "123")
    notifier.enqueue("Rate limited message")
    await notifier.client.aclose()
    notifier.client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                429, json={"ok": False, "parameters": {"retry_after": 42}}
            )
        )
    )
    await notifier.deliver()
    assert store.next_notification(1041) is None
    assert store.next_notification(1042)["attempts"] == 1
    await notifier.close()
    store.close()
