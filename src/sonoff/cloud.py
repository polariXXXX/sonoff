import asyncio
import base64
import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx

from .config import Socket

HOSTS = {
    "eu": "https://eu-apia.coolkit.cc",
    "us": "https://us-apia.coolkit.cc",
    "as": "https://as-apia.coolkit.cc",
    "cn": "https://cn-apia.coolkit.cn",
}


def sign(secret: str, message: str):
    return base64.b64encode(
        hmac.new(secret.encode(), message.encode(), hashlib.sha256).digest()
    ).decode()


def decode_device(item):
    data = item.get("itemData", {})
    return {
        "device_id": data.get("deviceid"),
        "name": data.get("name", "Sonoff"),
        "online": data.get("online", False),
        "params": data.get("params", {}),
        "uiid": data.get("extra", {}).get("uiid"),
        "item_type": item.get("itemType", 1),
        "updated_at": time.time(),
    }


def socket_status(device, channel):
    value = device.get("params", {}).get("switch")
    if channel is not None:
        value = next(
            (
                s.get("switch")
                for s in device.get("params", {}).get("switches", [])
                if s.get("outlet") == channel
            ),
            None,
        )
    return {
        "confirmed": value if value in ("on", "off") else None,
        "online": device.get("online"),
        "updated_at": device.get("updated_at"),
    }


class MockCloud:
    def __init__(self):
        self.devices = {}
        self.error = None
        self.connected = True

    def status(self, socket):
        return socket_status(self.devices.get(socket.device_id, {}), socket.channel)

    async def switch(self, socket, action, valid=lambda: True):
        if not valid():
            raise ValueError("Obsolete command")
        params = (
            {"switch": action}
            if socket.channel is None
            else {"switches": [{"outlet": socket.channel, "switch": action}]}
        )
        self.devices[socket.device_id] = {
            "device_id": socket.device_id,
            "name": socket.name,
            "online": True,
            "params": params,
            "updated_at": time.time(),
        }

    async def poll(self, sockets):
        for socket in sockets:
            self.devices.setdefault(
                socket.device_id,
                {
                    "device_id": socket.device_id,
                    "name": socket.name,
                    "online": True,
                    "params": {},
                    "updated_at": time.time(),
                },
            )

    async def discover(self):
        return list(self.devices.values())

    async def close(self):
        pass


def load_tokens(path: Path):
    payload = json.loads(path.read_text())
    data = payload.get("data", payload)
    region = payload.get("region", data.get("region"))
    if region not in HOSTS:
        raise ValueError("Token file must contain region eu/us/as/cn")
    return {
        "at": data["accessToken"],
        "rt": data["refreshToken"],
        "region": region,
        "expires": data["atExpiredTime"] / 1000,
    }


class EWeLink:
    def __init__(self, app_id, secret, store, redirect_url, token_file=None):
        if not app_id or not secret:
            raise ValueError("EWL_APPID and EWL_SECRET are required")
        self.app_id, self.secret, self.store, self.redirect_url = (
            app_id,
            secret,
            store,
            redirect_url,
        )
        self.tokens = store.get("tokens")
        if self.tokens is None and token_file:
            self.tokens = load_tokens(Path(token_file))
            store.put("tokens", self.tokens)
        self.client = httpx.AsyncClient(timeout=10)
        self.lock, self.refresh_lock = asyncio.Lock(), asyncio.Lock()
        self.last_request = 0
        self.devices = {}
        self.error = None
        self.connected = False
        self.oauth_states = {}

    def authorization_url(self):
        if not self.redirect_url.startswith("https://"):
            raise ValueError("Configure HTTPS EWL_REDIRECT_URL first")
        now = time.monotonic()
        self.oauth_states = {k: v for k, v in self.oauth_states.items() if v > now}
        state = secrets.token_urlsafe(32)
        self.oauth_states[state] = now + 600
        seq = str(int(time.time() * 1000))
        return "https://c2ccdn.coolkit.cc/oauth/index.html?" + urlencode(
            {
                "clientId": self.app_id,
                "seq": seq,
                "authorization": sign(self.secret, f"{self.app_id}_{seq}"),
                "redirectUrl": self.redirect_url,
                "grantType": "authorization_code",
                "state": state,
                "nonce": secrets.token_hex(4),
                "showQRCode": "false",
            }
        )

    async def raw_request(
        self, region, method, path, body=None, access=None, params=None, valid=lambda: True
    ):
        if region not in HOSTS:
            raise ValueError("Invalid eWeLink region")
        content = (
            json.dumps(body, separators=(",", ":"), ensure_ascii=False)
            if body is not None
            else None
        )
        signature = sign(self.secret, content or "")
        headers = {
            "X-CK-Appid": self.app_id,
            "X-CK-Nonce": secrets.token_hex(4),
            "Authorization": f"Bearer {access}" if access else f"Sign {signature}",
            "Content-Type": "application/json",
        }
        async with self.lock:
            await asyncio.sleep(max(0, 1 - (time.monotonic() - self.last_request)))
            if not valid():
                raise ValueError("Obsolete command")
            self.last_request = time.monotonic()
            response = await self.client.request(
                method,
                HOSTS[region] + path,
                headers=headers,
                content=content,
                params=params,
            )
        response.raise_for_status()
        result = response.json()
        if result.get("error", 0):
            raise CloudError(result["error"])
        return result.get("data", {})

    async def authorize(self, code, region, state):
        expires = self.oauth_states.pop(state, 0)
        if expires < time.monotonic():
            raise ValueError("Invalid or expired OAuth state")
        data = await self.raw_request(
            region,
            "POST",
            "/v2/user/oauth/token",
            {
                "code": code,
                "redirectUrl": self.redirect_url,
                "grantType": "authorization_code",
            },
        )
        self.tokens = {
            "at": data["accessToken"],
            "rt": data["refreshToken"],
            "region": region,
            "expires": data["atExpiredTime"] / 1000,
        }
        self.store.put("tokens", self.tokens)

    async def refresh(self, force=False):
        async with self.refresh_lock:
            if not self.tokens:
                raise ValueError("Connect eWeLink account first")
            if not force and self.tokens["expires"] > time.time() + 300:
                return
            data = await self.raw_request(
                self.tokens["region"],
                "POST",
                "/v2/user/refresh",
                {
                    "rt": self.tokens["rt"],
                },
            )
            self.tokens = {
                **self.tokens,
                "at": data["at"],
                "rt": data["rt"],
                "expires": time.time() + 30 * 86400,
            }
            self.store.put("tokens", self.tokens)

    async def request(self, method, path, body=None, params=None, valid=lambda: True):
        await self.refresh()
        try:
            return await self.raw_request(
                self.tokens["region"], method, path, body, self.tokens["at"], params, valid
            )
        except CloudError as error:
            if error.code != 402:
                raise
            # Refresh credentials, but do not replay a command after waiting for network I/O.
            # The automation loop revalidates the action before the next attempt.
            await self.refresh(force=True)
            raise

    def status(self, socket):
        result = socket_status(self.devices.get(socket.device_id, {}), socket.channel)
        if not self.connected:
            result["online"] = None
        return result

    async def discover(self):
        data = await self.request("GET", "/v2/device/thing", params={"num": 0})
        devices = [
            decode_device(x) for x in data.get("thingList", []) if x.get("itemType") in (1, 2)
        ]
        self.devices.update({d["device_id"]: d for d in devices})
        self.connected, self.error = True, None
        return devices

    async def poll(self, sockets):
        if not sockets:
            return
        try:
            data = await self.request(
                "POST",
                "/v2/device/thing",
                {
                    "thingList": [
                        {
                            "itemType": self.devices.get(s.device_id, {}).get("item_type", 1),
                            "id": s.device_id,
                        }
                        for s in sockets
                    ],
                },
            )
            returned = set()
            for item in data.get("thingList", []):
                device = decode_device(item)
                returned.add(device["device_id"])
                self.devices[device["device_id"]] = device
            for socket in sockets:
                if socket.device_id not in returned:
                    self.devices.pop(socket.device_id, None)
            self.connected, self.error = True, None
        except Exception:
            self.connected, self.error = False, "eWeLink недоступний або потрібна авторизація"

    async def switch(self, socket: Socket, action: str, valid=lambda: True):
        device = self.devices.get(socket.device_id)
        if not device or not device.get("online"):
            raise ValueError("Device is unavailable; sync before control")
        params = device.get("params", {})
        if socket.channel is None:
            if "switch" not in params or "switches" in params:
                raise ValueError("Select a valid channel or supported single-switch device")
            payload = {"switch": action}
        else:
            if not any(s.get("outlet") == socket.channel for s in params.get("switches", [])):
                raise ValueError("Unsupported device channel")
            payload = {"switches": [{"outlet": socket.channel, "switch": action}]}
        await self.request(
            "POST",
            "/v2/device/thing/status",
            {
                "type": 1,
                "id": socket.device_id,
                "params": payload,
            },
            valid=valid,
        )
        # Read back instead of treating HTTP acceptance as observed device state.
        data = await self.request(
            "GET", "/v2/device/thing/status", params={"type": 1, "id": socket.device_id}
        )
        device["params"], device["updated_at"] = data.get("params", {}), time.time()
        if self.status(socket)["confirmed"] != action:
            raise ValueError("Device has not confirmed requested state")

    async def close(self):
        await self.client.aclose()


class CloudError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(f"eWeLink error {code}")
