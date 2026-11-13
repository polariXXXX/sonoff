import asyncio

import httpx

from sonoff.images import DeviceImages, allowed_url, image_key
from sonoff.store import Store

JPEG = b"\xff\xd8\xff" + b"test image"


async def test_new_device_resolves_photo_and_persists_cache(tmp_path):
    store = Store(tmp_path)
    images = DeviceImages(store)
    requests = []

    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(200, content=JPEG, headers={"content-type": "image/jpeg"})

    await images.client.aclose()
    images.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    device = {"brand": "SONOFF", "product_model": "THR316D"}
    images.schedule(device)
    assert images.decorate(device)["image_status"] == "searching"
    await asyncio.gather(*images.tasks.values())
    result = images.decorate(device)
    assert result["image_status"] == "found"
    assert result["image_url"].startswith("/api/device-images/")
    assert images.path(image_key(device)).read_bytes() == JPEG
    images.schedule(device)
    assert not images.tasks
    assert len(requests) == 1
    await images.close()
    restarted = DeviceImages(store)
    assert restarted.decorate(device)["image_status"] == "found"
    await restarted.close()
    store.close()


async def test_uncatalogued_model_searches_manufacturer_media_index(tmp_path):
    store = Store(tmp_path)
    images = DeviceImages(store)

    def handler(request):
        if request.url.path.endswith("/media"):
            assert request.url.params["search"] == "S26R2"
            return httpx.Response(
                200,
                json=[
                    {
                        "title": {"rendered": "SONOFF S26R2"},
                        "source_url": "https://itead.cc/wp-content/uploads/s26r2.jpg",
                    },
                    {
                        "title": {"rendered": "Other product"},
                        "source_url": "https://itead.cc/wrong.jpg",
                    },
                ],
            )
        assert request.url.path.endswith("s26r2.jpg")
        return httpx.Response(200, content=JPEG, headers={"content-type": "image/jpeg"})

    await images.client.aclose()
    images.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    device = {"brand": "SONOFF", "product_model": "S26R2"}
    images.schedule(device)
    await asyncio.gather(*images.tasks.values())
    assert images.decorate(device)["image_status"] == "found"
    await images.close()
    store.close()


async def test_no_search_results_produce_honest_not_found_and_allow_retry(tmp_path):
    store = Store(tmp_path)
    images = DeviceImages(store)
    await images.client.aclose()
    images.client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[]))
    )
    device = {"brand": "SONOFF", "product_model": "UNKNOWN"}
    images.schedule(device)
    await asyncio.gather(*images.tasks.values())
    assert images.decorate(device)["image_status"] == "not_found"
    images.schedule(device)
    assert not images.tasks
    images.schedule(device, force=True)
    assert images.tasks
    await images.close()
    store.close()


async def test_image_redirect_cannot_reach_untrusted_server(tmp_path):
    store = Store(tmp_path)
    images = DeviceImages(store)
    requests = []

    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})

    await images.client.aclose()
    images.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    device = {"brand": "SONOFF", "product_model": "THR316D"}
    images.schedule(device)
    await asyncio.gather(*images.tasks.values())
    assert images.decorate(device)["image_status"] == "not_found"
    assert len(requests) == 1
    await images.close()
    store.close()


def test_image_urls_reject_credentials_private_hosts_and_wrong_scheme():
    assert not allowed_url("http://itead.cc/photo.jpg")
    assert not allowed_url("https://127.0.0.1/photo.jpg")
    assert not allowed_url("https://user:password@itead.cc/photo.jpg")
    assert not allowed_url("https://i0.wp.com/other.example/private.jpg")
    assert allowed_url("https://i0.wp.com/itead.cc/wp-content/uploads/photo.jpg")
