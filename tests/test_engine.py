import asyncio

import pytest

from sonoff.cloud import MockCloud
from sonoff.config import Config, Rule, SensorConfig, Socket
from sonoff.engine import Engine
from sonoff.store import Store


class Clock:
    now = 0

    def __call__(self):
        return self.now


@pytest.fixture
def setup(tmp_path):
    clock, cloud, store = Clock(), MockCloud(), Store(tmp_path)
    config = Config(
        sensor=SensorConfig(debounce_seconds=0), sockets=[Socket(device_id="boiler", name="Бойлер")]
    )
    engine = Engine(config, cloud, store, clock)
    yield engine, cloud, clock, store
    store.close()


async def test_outage_off_immediate_and_return_waits_twenty_minutes(setup):
    engine, cloud, clock, _ = setup
    engine.sample(False)
    await engine.tick()
    assert cloud.status(engine.config.sockets[0])["confirmed"] == "off"
    engine.sample(True)
    clock.now = 1199
    await engine.tick()
    assert cloud.status(engine.config.sockets[0])["confirmed"] == "off"
    clock.now = 1200
    await engine.tick()
    assert cloud.status(engine.config.sockets[0])["confirmed"] == "on"


async def test_brief_interruption_restarts_full_delay(setup):
    engine, cloud, clock, _ = setup
    engine.sample(True)
    clock.now = 1199
    engine.sample(False)
    await engine.tick()
    engine.sample(True)
    clock.now = 1201
    await engine.tick()
    assert cloud.status(engine.config.sockets[0])["confirmed"] == "off"
    clock.now = 2399
    await engine.tick()
    assert cloud.status(engine.config.sockets[0])["confirmed"] == "on"


async def test_sockets_have_independent_delays(setup):
    engine, cloud, clock, _ = setup
    engine.update(
        Socket(device_id="other", name="Other", mains_present=Rule(action="on", delay_minutes=1))
    )
    engine.sample(True)
    clock.now = 60
    await engine.tick()
    assert cloud.status(engine.config.sockets[1])["confirmed"] == "on"
    assert cloud.status(engine.config.sockets[0])["confirmed"] is None


async def test_unknown_cancels_pending_on(setup):
    engine, cloud, clock, _ = setup
    engine.sample(True)
    engine.sample(None)
    clock.now = 1300
    await engine.tick()
    assert cloud.status(engine.config.sockets[0])["confirmed"] is None


async def test_manual_on_is_independent_of_sensor_until_auto_restored(setup):
    engine, cloud, _, _ = setup
    engine.sample(True)
    engine.manual("boiler", "on")
    await engine.tick()
    assert cloud.status(engine.config.sockets[0])["confirmed"] == "on"
    engine.sample(False)
    await engine.tick()
    assert cloud.status(engine.config.sockets[0])["confirmed"] == "on"
    engine.sample(True)
    assert engine.runtime["boiler"].due is None
    engine.sample(False)
    engine.auto("boiler")
    await engine.tick()
    assert cloud.status(engine.config.sockets[0])["confirmed"] == "off"


async def test_config_change_invalidates_existing_deadline(setup):
    engine, _, clock, _ = setup
    engine.sample(True)
    clock.now = 600
    engine.update(Socket(device_id="boiler", name="Changed"))
    assert engine.runtime["boiler"].due == 1800


async def test_retry_does_not_replay_obsolete_on(setup):
    engine, _, clock, _ = setup

    class FailingCloud(MockCloud):
        actions = []

        async def switch(self, socket, action, valid=lambda: True):
            self.actions.append(action)
            raise OSError("offline")

    engine.cloud = cloud = FailingCloud()
    engine.sample(True)
    clock.now = 1200
    await engine.tick()
    engine.sample(False)
    clock.now = 1220
    await engine.tick()
    assert cloud.actions == ["on", "off"]


async def test_command_is_rechecked_after_network_queue_wait(setup):
    engine, _, clock, _ = setup
    entered, resume = asyncio.Event(), asyncio.Event()

    class DelayedCloud(MockCloud):
        async def switch(self, socket, action, valid=lambda: True):
            entered.set()
            await resume.wait()
            await super().switch(socket, action, valid)

    engine.cloud = cloud = DelayedCloud()
    engine.sample(True)
    clock.now = 1200
    task = asyncio.create_task(engine.tick())
    await entered.wait()
    engine.sample(False)
    resume.set()
    await task
    assert cloud.status(engine.config.sockets[0])["confirmed"] is None
    await engine.tick()
    assert cloud.status(engine.config.sockets[0])["confirmed"] == "off"


def test_debounce_presence_but_not_absence(setup):
    engine, _, clock, _ = setup
    engine.config.sensor.debounce_seconds = 0.3
    engine.sample(True)
    assert engine.present is None
    clock.now = 0.3
    engine.sample(True)
    assert engine.present is True
    engine.sample(False)
    assert engine.present is False


def test_restart_does_not_restore_elapsed_on_delay(setup):
    engine, cloud, clock, store = setup
    engine.sample(True)
    clock.now = 1190
    replacement = Engine(engine.config, cloud, store, clock)
    replacement.sample(True)
    assert replacement.runtime["boiler"].due == 2390


async def test_countdown_ticks_and_reaches_zero_after_manual_on(setup):
    engine, cloud, clock, _ = setup
    engine.sample(True)
    assert engine.snapshot()["sockets"][0]["turn_on_remaining_seconds"] == 1200
    clock.now = 123
    assert engine.snapshot()["sockets"][0]["turn_on_remaining_seconds"] == 1077
    engine.manual("boiler", "on")
    await engine.tick()
    snapshot = engine.snapshot()["sockets"][0]
    assert snapshot["confirmed"] == "on"
    assert snapshot["turn_on_remaining_seconds"] == 0
    assert snapshot["turn_on_scheduled"] is False


async def test_external_early_on_cancels_timer_and_sends_no_duplicate(setup):
    engine, cloud, clock, _ = setup
    engine.sample(True)
    clock.now = 100
    await cloud.switch(engine.config.sockets[0], "on")
    await engine.tick()
    assert engine.runtime["boiler"].due is None
    assert engine.snapshot()["sockets"][0]["turn_on_remaining_seconds"] == 0
    original_update = cloud.status(engine.config.sockets[0])["updated_at"]
    clock.now = 1200
    await engine.tick()
    assert cloud.status(engine.config.sockets[0])["updated_at"] == original_update


async def test_offline_last_known_on_does_not_cancel_pending_on(setup):
    engine, cloud, _, _ = setup
    engine.sample(True)
    cloud.devices["boiler"] = {"online": False, "params": {"switch": "on"}}
    await engine.tick()
    assert engine.runtime["boiler"].due == 1200


def test_no_on_countdown_when_only_off_is_pending(setup):
    engine, _, _, _ = setup
    engine.sample(False)
    snapshot = engine.snapshot()["sockets"][0]
    assert snapshot["desired"] == "off"
    assert snapshot["turn_on_remaining_seconds"] == 0
    assert snapshot["turn_on_scheduled"] is False


async def test_removing_socket_cancels_on_and_does_not_switch_device(setup):
    engine, cloud, clock, store = setup
    engine.sample(True)
    engine.remove("boiler")
    clock.now = 1300
    await engine.tick()
    assert engine.config.sockets == []
    assert engine.runtime == {}
    assert cloud.devices == {}
    assert store.get("config")["sockets"] == []


async def test_removing_socket_invalidates_command_waiting_for_network(setup):
    engine, _, clock, _ = setup
    entered, resume = asyncio.Event(), asyncio.Event()

    class DelayedCloud(MockCloud):
        async def switch(self, socket, action, valid=lambda: True):
            entered.set()
            await resume.wait()
            await super().switch(socket, action, valid)

    engine.cloud = cloud = DelayedCloud()
    engine.sample(True)
    clock.now = 1200
    task = asyncio.create_task(engine.tick())
    await entered.wait()
    engine.remove("boiler")
    resume.set()
    await task
    assert cloud.devices == {}
    assert engine.snapshot()["sockets"] == []


@pytest.mark.parametrize("signal", [None, False, True])
async def test_manual_commands_survive_sensor_change_before_execution(setup, signal):
    engine, cloud, _, _ = setup
    engine.sample(signal)
    engine.manual("boiler", "on")
    engine.sample(False if signal is not False else None)
    await engine.tick()
    assert cloud.status(engine.config.sockets[0])["confirmed"] == "on"
    engine.manual("boiler", "off")
    engine.sample(True)
    await engine.tick()
    assert cloud.status(engine.config.sockets[0])["confirmed"] == "off"
