import asyncio
import hashlib
import re
import time
from urllib.parse import urlsplit

import httpx

# Verified manufacturer catalogue entries; the search below handles additional models.
CATALOGUE = {
    "THR316": "https://i0.wp.com/itead.cc/wp-content/uploads/2022/09/TH-Origin.jpg",
    "THR320": "https://i0.wp.com/itead.cc/wp-content/uploads/2022/09/TH-Origin.jpg",
    "THR316D": "https://i0.wp.com/itead.cc/wp-content/uploads/2022/09/TH-Elite-.jpg",
    "THR320D": "https://i0.wp.com/itead.cc/wp-content/uploads/2022/09/TH-Elite-.jpg",
}
IMAGE_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
MAX_IMAGE_BYTES = 2 * 1024 * 1024


def allowed_url(url):
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
    ):
        return False
    if parsed.hostname in (
        "itead.cc",
        "sonoff.tech",
        "eu-ota.coolkit.cc",
        "us-ota.coolkit.cc",
        "as-ota.coolkit.cc",
        "cn-ota.coolkit.cn",
    ):
        return True
    return parsed.hostname in ("i0.wp.com", "i1.wp.com", "i2.wp.com") and parsed.path.startswith(
        "/itead.cc/"
    )


def image_key(device):
    brand = str(device.get("brand") or "SONOFF").strip().upper()
    model = str(device.get("product_model") or "").strip().upper()
    return hashlib.sha256(f"{brand}:{model}".encode()).hexdigest() if model else None


class DeviceImages:
    def __init__(self, store):
        self.store = store
        self.directory = store.directory / "images"
        self.directory.mkdir(exist_ok=True, mode=0o700)
        self.client = httpx.AsyncClient(timeout=8, follow_redirects=False)
        self.tasks = {}
        self.semaphore = asyncio.Semaphore(1)

    def decorate(self, device):
        key = image_key(device)
        record = self.store.get(f"image:{key}") if key else None
        result = {
            "image_status": "pending"
            if not key
            else ("found" if device.get("image_is_generic") is False else "not_found"),
            "image_error": None,
        }
        if record:
            result.update(image_status=record["status"], image_error=record.get("error"))
            if record["status"] == "found" and (self.directory / record["filename"]).is_file():
                result.update(image_url=f"/api/device-images/{key}", image_is_generic=False)
            elif record["status"] == "found":
                result["image_status"] = "pending"
        if key in self.tasks:
            result["image_status"] = "searching"
        return result

    def schedule(self, device, force=False):
        key = image_key(device)
        if not key or key in self.tasks:
            return
        previous = self.store.get(f"image:{key}")
        if previous and not force:
            if previous["status"] == "found" and (self.directory / previous["filename"]).is_file():
                return
            if previous["status"] != "found" and time.time() - previous["checked_at"] < 86400:
                return
        self.tasks[key] = asyncio.create_task(self.resolve(dict(device), key))

    async def candidates(self, device):
        # The cloud may provide an actual image URL; a brand logo is never a device image.
        candidate = device.get("model_image_url")
        if isinstance(candidate, str) and allowed_url(candidate):
            yield candidate
        model = str(device.get("product_model") or "").strip().upper()
        brand = str(device.get("brand") or "SONOFF").upper()
        if brand != "SONOFF":
            return
        if model in CATALOGUE:
            yield CATALOGUE[model]
            return
        if not re.fullmatch(r"[A-Z0-9_-]{1,64}", model):
            return
        # Query the manufacturer's media index by the exact model, not general web images.
        response = await self.client.get(
            "https://itead.cc/wp-json/wp/v2/media",
            params={"search": model, "per_page": 10},
        )
        response.raise_for_status()
        items = response.json()
        if not isinstance(items, list):
            return
        pattern = re.compile(rf"(?<![A-Z0-9]){re.escape(model)}(?![A-Z0-9])", re.I)
        for item in items:
            description = (
                str(item.get("alt_text", "")) + " " + str(item.get("title", {}).get("rendered", ""))
            )
            url = item.get("source_url")
            if pattern.search(description) and isinstance(url, str) and allowed_url(url):
                yield url

    async def download(self, url, key):
        for _ in range(4):
            if not allowed_url(url):
                raise ValueError("Untrusted image URL")
            async with self.client.stream("GET", url) as response:
                if response.is_redirect:
                    url = str(response.url.join(response.headers["Location"]))
                    continue
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").split(";")[0].lower()
                extension = IMAGE_TYPES.get(content_type)
                if not extension:
                    raise ValueError("Unsupported image format")
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_IMAGE_BYTES:
                        raise ValueError("Image exceeds size limit")
                valid = (
                    (extension == "jpg" and content.startswith(b"\xff\xd8\xff"))
                    or (extension == "png" and content.startswith(b"\x89PNG\r\n\x1a\n"))
                    or (extension == "webp" and content[:4] == b"RIFF" and content[8:12] == b"WEBP")
                )
                if not valid:
                    raise ValueError("Response is not an image")
                filename = f"{key}.{extension}"
                temporary = self.directory / f"{key}.tmp"
                temporary.write_bytes(content)
                temporary.replace(self.directory / filename)
                return filename
        raise ValueError("Too many image redirects")

    async def resolve(self, device, key):
        try:
            async with self.semaphore:
                async for url in self.candidates(device):
                    try:
                        filename = await self.download(url, key)
                    except (httpx.HTTPError, ValueError):
                        continue
                    self.store.put(
                        f"image:{key}",
                        {"status": "found", "filename": filename, "checked_at": time.time()},
                    )
                    return
                self.store.put(f"image:{key}", {"status": "not_found", "checked_at": time.time()})
        except Exception:
            self.store.put(
                f"image:{key}",
                {
                    "status": "error",
                    "checked_at": time.time(),
                    "error": "Каталог зображень тимчасово недоступний",
                },
            )
        finally:
            self.tasks.pop(key, None)

    def path(self, key):
        if not re.fullmatch(r"[a-f0-9]{64}", key):
            return None
        record = self.store.get(f"image:{key}")
        if not record or record["status"] != "found":
            return None
        path = self.directory / record["filename"]
        return path if path.is_file() else None

    async def close(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.client.aclose()
