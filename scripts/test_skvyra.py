"""Authorized live smoke test of Skvyra only; restore its observed initial state."""

import asyncio
import tempfile
from pathlib import Path

from sonoff.cloud import EWeLink
from sonoff.config import Socket
from sonoff.store import Store

TARGET = "10025391ef"


def credentials():
    result = {}
    for line in Path("../.env").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip().strip("\"'")
    return result


async def main():
    values = credentials()
    with tempfile.TemporaryDirectory(prefix="sonoff-live-") as directory:
        store = Store(Path(directory))
        cloud = EWeLink(
            values["EWL_APPID"], values["EWL_SECRET"], store, "", token_file="../tokens.json"
        )
        initial = None
        socket = Socket(device_id=TARGET, name="Skvyra", mode="manual")
        try:
            await cloud.poll([socket])
            observed = cloud.status(socket)
            if observed["online"] is not True or observed["confirmed"] not in ("on", "off"):
                print("Target unavailable or unsupported; no switching performed.")
                return False
            initial = observed["confirmed"]
            print(f"Skvyra initial confirmed state: {initial}")
            for action in ("off", "on"):
                await cloud.switch(socket, action)
                print(f"Skvyra requested {action}; confirmed {cloud.status(socket)['confirmed']}")
            return True
        except Exception as error:
            # Do not print response bodies, request URLs, headers or token-bearing exceptions.
            print(f"Live test failed: {type(error).__name__}")
            return False
        finally:
            try:
                if initial is not None:
                    await cloud.switch(socket, initial)
                    print(f"Skvyra restored and confirmed: {cloud.status(socket)['confirmed']}")
            finally:
                await cloud.close()
                store.close()


if __name__ == "__main__":
    raise SystemExit(0 if asyncio.run(main()) else 1)
