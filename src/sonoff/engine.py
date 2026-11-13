import asyncio
import time
from dataclasses import dataclass, field

from .config import Config, Socket


@dataclass
class Runtime:
    desired: str | None = None
    due: float | None = None
    generation: int = 0
    error: str | None = None
    attempts: int = 0
    manual_request: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class Engine:
    """Timers are disposable: startup and every raw reversal invalidate ON deadlines."""

    def __init__(self, config: Config, cloud, store, clock=time.monotonic, notifier=None):
        self.config, self.cloud, self.store, self.clock = config, cloud, store, clock
        self.notifier = notifier
        self.raw = None
        self.present = None
        self.changed = clock()
        self.runtime = {s.device_id: Runtime() for s in config.sockets}

    def schedule(self, socket: Socket):
        state = self.runtime[socket.device_id]
        state.generation += 1
        state.desired, state.due, state.error = None, None, None
        state.attempts, state.manual_request = 0, False
        safety_off = self.present is False and socket.mains_absent.action == "off"
        if self.present is None or (socket.mode != "auto" and not safety_off):
            return
        rule = socket.mains_present if self.present else socket.mains_absent
        if rule.action != "hold":
            state.desired = rule.action
            state.due = self.clock() + rule.delay_minutes * 60

    def sample(self, raw: bool | None):
        now = self.clock()
        if raw != self.raw:
            self.raw, self.changed = raw, now
            # Any interruption cancels an ON, even if shorter than the input debounce.
            self.present = None
            for socket in self.config.sockets:
                self.schedule(socket)
        # Absence/unknown immediately prevents ON. Presence requires debounce first.
        stable = (
            raw
            if raw is False
            else (
                True
                if raw is True and now - self.changed >= self.config.sensor.debounce_seconds
                else None
            )
        )
        if stable != self.present:
            self.present = stable
            if self.notifier:
                self.notifier.mains(stable)
            self.store.event(f"Мережа: { {True: 'є', False: 'немає', None: 'невідомо'}[stable] }")
            for socket in self.config.sockets:
                self.schedule(socket)

    def update(self, socket: Socket):
        for i, existing in enumerate(self.config.sockets):
            if existing.device_id == socket.device_id:
                self.config.sockets[i] = socket
                break
        else:
            if len(self.config.sockets) >= 10:
                raise ValueError("Maximum 10 sockets")
            self.config.sockets.append(socket)
            self.runtime[socket.device_id] = Runtime()
        self.schedule(socket)
        self.store.put("config", self.config.model_dump())
        self.store.event(f"Правила оновлено: {socket.name}")

    def remove(self, device_id: str):
        socket = next(s for s in self.config.sockets if s.device_id == device_id)
        state = self.runtime.pop(device_id)
        state.generation += 1
        state.desired, state.due, state.manual_request = None, None, False
        self.config.sockets = [s for s in self.config.sockets if s.device_id != device_id]
        self.store.put("config", self.config.model_dump())
        self.store.event(f"Пристрій видалено зі списку: {socket.name}")

    def manual(self, device_id: str, action: str):
        socket = next(s for s in self.config.sockets if s.device_id == device_id)
        if action == "on" and self.present is not True:
            raise ValueError("ON requires confirmed mains presence")
        socket.mode = "manual"
        self.schedule(socket)
        state = self.runtime[device_id]
        state.desired, state.due, state.manual_request = action, self.clock(), True
        self.store.put("config", self.config.model_dump())
        self.store.event(f"Ручне {action.upper()}: {socket.name}")

    def auto(self, device_id: str):
        socket = next(s for s in self.config.sockets if s.device_id == device_id)
        socket.mode = "auto"
        self.schedule(socket)
        self.store.put("config", self.config.model_dump())

    async def execute(self, socket: Socket):
        state = self.runtime.get(socket.device_id)
        if state is None:
            return
        async with state.lock:
            socket = next((s for s in self.config.sockets if s.device_id == socket.device_id), None)
            if socket is None:
                return
            observed = self.cloud.status(socket)
            if (
                state.desired == "on"
                and state.due is not None
                and observed["online"] is True
                and observed["confirmed"] == "on"
            ):
                if state.attempts > 0 and self.notifier:
                    self.notifier.switched(
                        socket.name, "on", "manual" if state.manual_request else "auto"
                    )
                state.generation += 1
                state.due, state.error, state.manual_request = None, None, False
                state.attempts = 0
                self.store.event(f"Розетка вже увімкнена; таймер ON скасовано: {socket.name}")
            if state.due is None or self.clock() < state.due:
                return
            if state.desired == "on" and self.present is not True:
                self.schedule(socket)
                return
            safety_off = self.present is False and state.desired == "off"
            if socket.mode != "auto" and not state.manual_request and not safety_off:
                return
            generation, desired = state.generation, state.desired
            source = "manual" if state.manual_request else "auto"
            try:
                await self.cloud.switch(
                    socket,
                    desired,
                    valid=lambda: generation == state.generation
                    and (desired != "on" or self.present is True),
                )
            except Exception:
                if generation == state.generation:
                    state.error = "Команда не підтверджена; повторна спроба"
                    state.attempts += 1
                    state.due = self.clock() + min(300, 5 * 2 ** min(state.attempts, 6))
                    self.store.event(f"Помилка керування: {socket.name}")
            else:
                self.store.event(f"Команда {desired.upper()} прийнята: {socket.name}")
                if self.notifier:
                    self.notifier.switched(socket.name, desired, source)
                if generation == state.generation:
                    state.due, state.error, state.manual_request = None, None, False

    async def tick(self):
        await asyncio.gather(*(self.execute(s) for s in list(self.config.sockets)))

    def snapshot(self):
        sockets = []
        for socket in self.config.sockets:
            state = self.runtime[socket.device_id]
            observed = self.cloud.status(socket)
            remaining = max(0, state.due - self.clock()) if state.due is not None else None
            on_scheduled = (
                state.desired == "on" and remaining is not None and observed["confirmed"] != "on"
            )
            sockets.append(
                {
                    **socket.model_dump(),
                    **observed,
                    "desired": state.desired,
                    "remaining_seconds": remaining,
                    "turn_on_scheduled": on_scheduled,
                    "turn_on_remaining_seconds": remaining if on_scheduled else 0,
                    "error": state.error,
                }
            )
        return {"mains": self.present, "sockets": sockets}
