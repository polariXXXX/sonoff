import time

import httpx
import pytest

from sonoff.cloud import CloudError, EWeLink, sign, socket_status
from sonoff.config import Socket
from sonoff.store import Store


def test_oauth_signature_matches_official_vector():
    assert sign("abc", "ABC_123") == "v1+mfNY2ukxswM8sZOTg99srZsVnUVv9DGXeav1096M="


def test_channel_status_does_not_assume_single_switch():
    device = {
        "online": True,
        "params": {"switches": [{"outlet": 0, "switch": "on"}, {"outlet": 1, "switch": "off"}]},
    }
    assert socket_status(device, 0)["confirmed"] == "on"
    assert socket_status(device, 1)["confirmed"] == "off"
    assert socket_status(device, None)["confirmed"] is None


async def test_bad_oauth_state_makes_no_network_request(tmp_path):
    store = Store(tmp_path)
    cloud = EWeLink("app", "secret", store, "https://example.com/oauth/callback")
    with pytest.raises(ValueError):
        await cloud.authorize("code", "eu", "invalid")
    await cloud.close()
    store.close()


async def test_token_refresh_persists_rotated_credentials(tmp_path):
    store = Store(tmp_path)
    cloud = EWeLink("app", "secret", store, "https://example.com/oauth/callback")
    cloud.tokens = {"at": "old", "rt": "old-refresh", "region": "eu", "expires": 0}
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"error": 0, "data": {"at": "new", "rt": "rotated"}})

    await cloud.client.aclose()
    cloud.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await cloud.refresh()
    assert store.get("tokens")["rt"] == "rotated"
    assert requests[0].url.path == "/v2/user/refresh"
    assert requests[0].headers["Authorization"].startswith("Sign ")
    await cloud.close()
    store.close()


async def test_expired_access_token_does_not_automatically_replay_command(tmp_path):
    store = Store(tmp_path)
    cloud = EWeLink("app", "secret", store, "https://example.com/oauth/callback")
    cloud.tokens = {"at": "old", "rt": "rt", "region": "eu", "expires": time.time() + 1000}
    calls = []

    async def raw(region, method, path, *args, **kwargs):
        calls.append(path)
        if path == "/v2/user/refresh":
            return {"at": "new", "rt": "new-rt"}
        raise CloudError(402)

    cloud.raw_request = raw
    with pytest.raises(CloudError):
        await cloud.request("POST", "/v2/device/thing/status", {"params": {"switch": "on"}})
    assert calls == ["/v2/device/thing/status", "/v2/user/refresh"]
    await cloud.close()
    store.close()


async def test_channel_validation_prevents_wrong_device_payload(tmp_path):
    store = Store(tmp_path)
    cloud = EWeLink("app", "secret", store, "https://example.com/oauth/callback")
    cloud.devices["multi"] = {
        "online": True,
        "params": {"switches": [{"outlet": 0, "switch": "off"}]},
    }
    with pytest.raises(ValueError):
        await cloud.switch(Socket(device_id="multi", name="multi"), "on")
    await cloud.close()
    store.close()


def test_token_file_import_normalizes_oauth_response(tmp_path):
    import json

    from sonoff.cloud import load_tokens

    path = tmp_path / "tokens.json"
    path.write_text(
        json.dumps(
            {
                "region": "eu",
                "data": {
                    "accessToken": "test-at",
                    "refreshToken": "test-rt",
                    "atExpiredTime": 123000,
                },
            }
        )
    )
    assert load_tokens(path) == {"at": "test-at", "rt": "test-rt", "region": "eu", "expires": 123}


def test_device_metadata_and_photo_mapping_do_not_expose_private_fields():
    from sonoff.cloud import decode_device

    device = decode_device(
        {
            "itemType": 1,
            "itemData": {
                "deviceid": "boiler",
                "productModel": "THR316",
                "brandName": "SONOFF",
                "devicekey": "private-key",
                "params": {
                    "switch": "on",
                    "currentTemperature": "14.6",
                    "currentHumidity": "66.6",
                    "rssi": -66,
                    "fwVersion": "1.3.0",
                    "ssid": "private-network",
                    "bindInfos": {"token": "secret"},
                },
            },
        }
    )
    status = socket_status(device, None)
    assert status["product_model"] == "THR316"
    assert status["temperature"] == "14.6"
    assert status["image_url"] == "/static/models/th-origin.jpg"
    assert "private-key" not in str(device)
    assert "private-network" not in str(device)
    assert "secret" not in str(device)


def test_unknown_model_uses_honest_generic_image():
    status = socket_status({"product_model": "unknown", "params": {}}, None)
    assert status["image_is_generic"] is True
    assert status["image_url"].endswith("device.svg")
