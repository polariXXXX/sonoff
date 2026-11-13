import asyncio
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .cloud import EWeLink, MockCloud
from .config import Config, Socket
from .engine import Engine
from .images import DeviceImages
from .notifications import Telegram
from .sensor import GPIOSensor, MockSensor
from .store import Store


class ManualCommand(BaseModel):
    action: str


class Simulation(BaseModel):
    present: bool | None


def create_app():
    security = HTTPBasic()
    control_token = secrets.token_urlsafe(32)
    username, password = os.getenv("WEB_USERNAME", "admin"), os.getenv("WEB_PASSWORD", "")
    if len(password) < 16 or password == "replace-with-a-long-random-password":
        raise ValueError("Set WEB_PASSWORD to a unique password of at least 16 characters")
    backend = os.getenv("CLOUD_BACKEND", "mock")
    if backend not in ("mock", "ewelink"):
        raise ValueError("CLOUD_BACKEND must be mock or ewelink")

    def authenticate(credentials: HTTPBasicCredentials = Depends(security)):
        valid_user = secrets.compare_digest(credentials.username.encode(), username.encode())
        valid_password = secrets.compare_digest(credentials.password.encode(), password.encode())
        if not (valid_user and valid_password):
            raise HTTPException(
                401, "Authentication required", headers={"WWW-Authenticate": "Basic"}
            )

    def mutation(request: Request, _=Depends(authenticate)):
        supplied = request.headers.get("X-Control-Token", "")
        if not secrets.compare_digest(supplied.encode(), control_token.encode()):
            raise HTTPException(403, "Invalid control token")

    @asynccontextmanager
    async def lifespan(app):
        os.umask(0o077)
        store = Store(Path(os.getenv("DATA_DIR", "data")))
        saved = store.get("config")
        config = (
            Config.model_validate(saved)
            if saved
            else Config.load(Path(os.getenv("CONFIG_PATH", "config/config.example.json")))
        )
        if os.getenv("SENSOR_BACKEND"):
            config.sensor.backend = os.environ["SENSOR_BACKEND"]
            config.sensor.chip = os.getenv("GPIO_CHIP", config.sensor.chip)
            config.sensor.line = int(os.getenv("GPIO_LINE", config.sensor.line))
            config = Config.model_validate(config.model_dump())
        if (
            backend == "ewelink"
            and config.sensor.backend == "mock"
            and os.getenv("ALLOW_SIMULATED_SENSOR") != "true"
        ):
            store.close()
            raise ValueError("Live cloud with mock sensor requires ALLOW_SIMULATED_SENSOR=true")
        cloud = (
            MockCloud()
            if backend == "mock"
            else EWeLink(
                os.getenv("EWL_APPID"),
                os.getenv("EWL_SECRET"),
                store,
                os.getenv("EWL_REDIRECT_URL", ""),
                os.getenv("EWL_TOKEN_FILE"),
            )
        )
        try:
            sensor = MockSensor() if config.sensor.backend == "mock" else GPIOSensor(config.sensor)
        except Exception:
            await cloud.close()
            store.close()
            raise
        telegram = Telegram(store, os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID"))
        images = DeviceImages(store)
        app.state.images = images
        engine = Engine(config, cloud, store, notifier=telegram)
        app.state.telegram = telegram
        app.state.engine, app.state.cloud, app.state.sensor, app.state.store = (
            engine,
            cloud,
            sensor,
            store,
        )
        app.state.sensor_error, app.state.last_sample = None, None
        app.state.control_token = control_token
        store.event("Сервіс запущено; затримки відраховуються заново")

        async def monitor():
            while True:
                try:
                    raw = sensor.read()
                    app.state.sensor_error = None
                except Exception:
                    raw = None
                    if app.state.sensor_error is None:
                        store.event("Помилка датчика; ON заблоковано")
                    app.state.sensor_error = "Не вдалося прочитати GPIO"
                engine.sample(raw)
                app.state.last_sample = time.monotonic()
                await asyncio.sleep(0.05)

        async def control():
            while True:
                await engine.tick()
                await asyncio.sleep(0.1)

        async def sync():
            while True:
                await cloud.poll(list(config.sockets))
                for socket in config.sockets:
                    images.schedule(cloud.devices.get(socket.device_id, {}))
                await asyncio.sleep(120)

        tasks = [asyncio.create_task(fn()) for fn in (monitor, control, sync, telegram.run)]
        app.state.tasks = tasks
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            sensor.close()
            await images.close()
            await telegram.close()
            await cloud.close()
            store.close()

    app = FastAPI(
        title="Sonoff Controller",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    static = Path(__file__).parent / "static"

    @app.middleware("http")
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        return response

    @app.get("/healthz")
    async def health():
        healthy = (
            not any(t.done() for t in app.state.tasks)
            and app.state.last_sample is not None
            and time.monotonic() - app.state.last_sample < 5
            and app.state.sensor_error is None
        )
        if not healthy:
            raise HTTPException(503, "Monitor unavailable")
        return {"status": "ok"}

    @app.get("/", dependencies=[Depends(authenticate)])
    async def home():
        return FileResponse(static / "index.html")

    @app.get("/api/state", dependencies=[Depends(authenticate)])
    async def state():
        snapshot = app.state.engine.snapshot()
        for socket in snapshot["sockets"]:
            socket.update(app.state.images.decorate(socket))
        return {
            **snapshot,
            "events": app.state.store.events(),
            "sensor_error": app.state.sensor_error,
            "cloud_error": app.state.cloud.error,
            "cloud_connected": app.state.cloud.connected,
            "simulation": backend == "mock",
            "sensor_simulation": isinstance(app.state.sensor, MockSensor),
            "control_token": control_token,
            "telegram": app.state.telegram.status(),
        }

    @app.put("/api/sockets/{device_id}", dependencies=[Depends(mutation)])
    async def configure(device_id: str, socket: Socket):
        if device_id != socket.device_id:
            raise HTTPException(400, "Device IDs must match")
        if backend == "ewelink" and device_id not in app.state.cloud.devices:
            raise HTTPException(400, "Discover and select a device first")
        try:
            app.state.engine.update(socket)
        except ValueError as error:
            raise HTTPException(400, str(error)) from None
        app.state.images.schedule(app.state.cloud.devices.get(device_id, {}))
        return {"ok": True}

    @app.delete("/api/sockets/{device_id}", dependencies=[Depends(mutation)])
    async def remove_socket(device_id: str):
        if device_id not in app.state.engine.runtime:
            raise HTTPException(404, "Unknown socket")
        app.state.engine.remove(device_id)
        return {"ok": True}

    @app.post("/api/sockets/{device_id}/control", dependencies=[Depends(mutation)])
    async def control(device_id: str, command: ManualCommand):
        if device_id not in app.state.engine.runtime:
            raise HTTPException(404, "Unknown socket")
        if command.action not in ("on", "off", "auto"):
            raise HTTPException(400, "Expected on, off or auto")
        try:
            if command.action == "auto":
                app.state.engine.auto(device_id)
            else:
                app.state.engine.manual(device_id, command.action)
        except ValueError as error:
            raise HTTPException(409, str(error)) from None
        return {"accepted": True}

    @app.post("/api/simulation", dependencies=[Depends(mutation)])
    async def simulate(payload: Simulation):
        if not isinstance(app.state.sensor, MockSensor):
            raise HTTPException(403, "Simulation is unavailable on GPIO")
        app.state.sensor.present = payload.present
        return {"ok": True}

    @app.post("/api/devices/discover", dependencies=[Depends(mutation)])
    async def discover():
        try:
            devices = await app.state.cloud.discover()
        except Exception:
            raise HTTPException(
                502, "Cannot obtain device list; check eWeLink authorization"
            ) from None
        # The cloud adapter exposes no devicekeys, apikeys or account tokens.
        return {"devices": devices}

    @app.post("/api/sockets/{device_id}/image", dependencies=[Depends(mutation)])
    async def find_image(device_id: str):
        if device_id not in app.state.engine.runtime:
            raise HTTPException(404, "Unknown socket")
        device = app.state.cloud.devices.get(device_id, {})
        if not device.get("product_model"):
            raise HTTPException(409, "Модель ще не отримано; виконайте пошук пристроїв")
        app.state.images.schedule(device, force=True)
        return {"accepted": True}

    @app.get("/api/device-images/{key}", dependencies=[Depends(authenticate)])
    async def device_image(key: str):
        path = app.state.images.path(key)
        if path is None:
            raise HTTPException(404, "Image not found")
        return FileResponse(path)

    @app.post("/api/oauth/start", dependencies=[Depends(mutation)])
    async def oauth_start():
        if not isinstance(app.state.cloud, EWeLink):
            raise HTTPException(409, "Enable CLOUD_BACKEND=ewelink first")
        try:
            return {"url": app.state.cloud.authorization_url()}
        except ValueError as error:
            raise HTTPException(400, str(error)) from None

    @app.get("/oauth/callback", dependencies=[Depends(authenticate)])
    async def oauth_callback(code: str, region: str, state: str):
        if not isinstance(app.state.cloud, EWeLink):
            raise HTTPException(409, "eWeLink is disabled")
        try:
            await app.state.cloud.authorize(code, region, state)
        except Exception:
            raise HTTPException(400, "OAuth failed; start authorization again") from None
        return RedirectResponse("/", status_code=303)

    app.mount("/static", StaticFiles(directory=static), name="static")
    return app
