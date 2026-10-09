"""Where kept audio lives when it is not in scripts.db: a Cloudflare R2 bucket.

§235, at the owner's direction. Kept audio (§132) sat in `scripts.db` beside
its script, on a 1 GB disk shared with every other database, under a 512 MB
ceiling - about 120 two-minute episodes before the least-played was evicted
and paid for again on the GPU. R2 holds as much as is kept, at $0.015 a GB a
month, and charges nothing for reading it back out.

**The database keeps the name; the bucket keeps the bytes.** `episode_audio`
still has one row per (script, voice) with everything a play needs except the
audio - sentences, `starts`, codec, length - plus `object_key`. Its old `pcm`
column becomes a hot copy, under AUDIO_CACHE_MAX_MB, so a popular episode is
read off local disk and only a cold one pays the round trip to R2.

**Two prefixes, so the bucket's own rules can be specific** (the owner's
condition for a bucket rule):

* `recent/` - every episode's audio, for its first week (`AUDIO_RECENT_DAYS`).
  The bucket deletes it after eight days (`deploy/r2-lifecycle.json`), one day
  after FAM stops reading it, so a sweep that is late by a few hours loses
  nothing.
* `kept/` - audio somebody saved, shared or vibed, copied there at a week old
  in the Infrequent Access class ($0.01 a GB a month, 30-day minimum). It is
  deleted by FAM when nobody holds it any more; no rule deletes it.

**No signed URLs, ever.** The server reads the object and streams it as PCM,
exactly as it streams everything else. A URL handed to the client would skip
the guest gate and the tier's replay count, and R2 charges nothing for the
server to read it.

Requests are signed with SigV4 over `httpx`, which FAM already depends on, in
about sixty lines, rather than pulling in boto3: botocore is ~50 MB resident on
a 512 MB plan, to make four kinds of request. `tests/test_audio_store.py` pins
the signature against botocore's for a fixed request.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
import logging
import threading
import time
import urllib.parse
from typing import Optional

from config import settings

log = logging.getLogger(__name__)

RECENT = "recent/"
KEPT = "kept/"
#: R2's name for its cheaper class, as the S3 API spells it.
INFREQUENT = "STANDARD_IA"

R2 = "r2"


class AudioStoreError(RuntimeError):
    """The bucket refused or could not be reached. Callers fall back."""


def object_name(prefix: str, audio_id: str) -> str:
    return f"{prefix}{audio_id}.opus"


def swap_prefix(name: str, prefix: str) -> str:
    return prefix + name.split("/", 1)[-1]


# -- SigV4 ------------------------------------------------------------------


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _quote(path: str) -> str:
    return urllib.parse.quote(path, safe="/-_.~")


def sign(method: str, host: str, path: str, headers: dict, payload: bytes,
         access_key: str, secret_key: str, now: _dt.datetime,
         region: str = "auto", service: str = "s3") -> dict:
    """The headers to send, Authorization included. Path-style, no query
    string - the only shape this module makes."""
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    day = now.strftime("%Y%m%d")
    out = {k.lower(): str(v).strip() for k, v in headers.items()}
    out["host"] = host
    out["x-amz-date"] = amz_date
    out["x-amz-content-sha256"] = hashlib.sha256(payload).hexdigest()
    names = sorted(out)
    canonical = "\n".join([
        method, _quote(path), "",
        "".join(f"{n}:{out[n]}\n" for n in names),
        ";".join(names), out["x-amz-content-sha256"]])
    scope = f"{day}/{region}/{service}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope,
                         hashlib.sha256(canonical.encode("utf-8")).hexdigest()])
    key = _hmac(_hmac(_hmac(_hmac(("AWS4" + secret_key).encode("utf-8"), day),
                            region), service), "aws4_request")
    signature = hmac.new(key, to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    out["authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
        f"SignedHeaders={';'.join(names)}, Signature={signature}")
    del out["host"]  # httpx sets it from the URL
    return out


# -- stores -----------------------------------------------------------------


class MemoryAudioStore:
    """The bucket's shape in a dict. Tests, and nothing else."""

    kind = "memory"

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.calls: list[tuple[str, str]] = []

    def put(self, name: str, data: bytes, storage_class: str = "") -> None:
        self.calls.append(("put", name))
        self.objects[name] = (bytes(data), storage_class or "STANDARD")

    def get(self, name: str) -> Optional[bytes]:
        self.calls.append(("get", name))
        hit = self.objects.get(name)
        return hit[0] if hit else None

    def copy(self, source: str, target: str, storage_class: str = "") -> None:
        self.calls.append(("copy", target))
        if source not in self.objects:
            raise AudioStoreError(f"no such object: {source}")
        self.objects[target] = (self.objects[source][0], storage_class or "STANDARD")

    def delete(self, name: str) -> None:
        self.calls.append(("delete", name))
        self.objects.pop(name, None)

    def describe(self) -> dict:
        return {"store": self.kind, "objects": len(self.objects)}


class R2AudioStore:
    """A Cloudflare R2 bucket over its S3 API."""

    kind = R2

    def __init__(self, account_id: str, bucket: str, access_key: str,
                 secret_key: str, timeout: float = 10.0) -> None:
        if not (account_id and bucket and access_key and secret_key):
            raise AudioStoreError(
                "AUDIO_STORE=r2 needs R2_ACCOUNT_ID, AUDIO_BUCKET,"
                " R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY")
        import httpx  # the module is importable without it; this store is not

        self.host = f"{account_id}.r2.cloudflarestorage.com"
        self.bucket = bucket
        self._keys = (access_key, secret_key)
        self._local = threading.local()
        self._timeout = httpx.Timeout(timeout, connect=3.0)
        self._httpx = httpx

    def _client(self):
        client = getattr(self._local, "client", None)
        if client is None:
            client = self._httpx.Client(timeout=self._timeout)
            self._local.client = client
        return client

    def _request(self, method: str, name: str, payload: bytes = b"",
                 headers: Optional[dict] = None):
        path = f"/{self.bucket}/{name}"
        signed = sign(method, self.host, path, headers or {}, payload,
                      self._keys[0], self._keys[1],
                      _dt.datetime.now(_dt.timezone.utc))
        try:
            return self._client().request(
                method, f"https://{self.host}{_quote(path)}",
                content=payload or None, headers=signed)
        except Exception as exc:
            raise AudioStoreError(f"{method} {name}: {exc.__class__.__name__}") from exc

    @staticmethod
    def _check(resp, what: str) -> None:
        if resp.status_code >= 300:
            raise AudioStoreError(f"{what}: HTTP {resp.status_code} {resp.text[:200]}")

    def put(self, name: str, data: bytes, storage_class: str = "") -> None:
        headers = {"content-type": "audio/ogg"}
        if storage_class:
            headers["x-amz-storage-class"] = storage_class
        self._check(self._request("PUT", name, bytes(data), headers), f"PUT {name}")

    def get(self, name: str) -> Optional[bytes]:
        resp = self._request("GET", name)
        if resp.status_code == 404:
            return None
        self._check(resp, f"GET {name}")
        return resp.content

    def copy(self, source: str, target: str, storage_class: str = "") -> None:
        headers = {"x-amz-copy-source": _quote(f"/{self.bucket}/{source}")}
        if storage_class:
            headers["x-amz-storage-class"] = storage_class
        resp = self._request("PUT", target, b"", headers)
        self._check(resp, f"COPY {source} -> {target}")
        # S3 can answer a failed copy with 200 and an <Error> body.
        if b"<Error>" in resp.content[:200]:
            raise AudioStoreError(f"COPY {source}: {resp.text[:200]}")

    def delete(self, name: str) -> None:
        resp = self._request("DELETE", name)
        if resp.status_code not in (200, 204, 404):
            self._check(resp, f"DELETE {name}")

    def describe(self) -> dict:
        return {"store": self.kind, "bucket": self.bucket}


_STORE = None
_STORE_ERROR = ""
_LOCK = threading.Lock()


def get_store():
    """The configured bucket, or None when audio stays in scripts.db.

    A misconfigured `AUDIO_STORE=r2` is None too - kept audio falls back to
    the database - and `status()` says why, which `/api/health` reports."""
    global _STORE, _STORE_ERROR
    with _LOCK:
        if _STORE is not None or _STORE_ERROR:
            return _STORE
        wanted = str(settings.audio_store or "").lower()
        if not wanted:
            return None
        if wanted != R2:
            _STORE_ERROR = f"AUDIO_STORE={wanted!r} is not a store FAM knows (r2)"
            log.error("audio store: %s", _STORE_ERROR)
            return None
        try:
            import credentials
            _STORE = R2AudioStore(
                settings.r2_account_id, settings.audio_bucket,
                credentials.active("R2_ACCESS_KEY_ID"),
                credentials.active("R2_SECRET_ACCESS_KEY"))
        except Exception as exc:
            _STORE_ERROR = str(exc)
            log.error("audio store: %s; kept audio stays in scripts.db", exc)
        return _STORE


def set_store(store) -> None:
    """Install a store (tests), or None to go back to the configuration."""
    global _STORE, _STORE_ERROR
    with _LOCK:
        _STORE, _STORE_ERROR = store, ""


def status() -> dict:
    """For `/api/health`."""
    store = get_store()
    if store is not None:
        return {**store.describe(), "recent_days": int(settings.audio_recent_days)}
    out = {"store": "scripts.db", "recent_days": int(settings.audio_recent_days)}
    if _STORE_ERROR:
        out["error"] = _STORE_ERROR
    return out


def verify(store=None) -> dict:
    """Write, read and delete one object (verify, do not inspect). For the
    boot check and `tools/verify_audio_store.py`."""
    store = store or get_store()
    if store is None:
        return {"ok": False, "why": status().get("error") or "no bucket configured"}
    name = f"{RECENT}_verify-{int(time.time())}.txt"
    body = b"fam audio store check"
    started = time.perf_counter()
    try:
        store.put(name, body)
        back = store.get(name)
        store.delete(name)
    except AudioStoreError as exc:
        return {"ok": False, "why": str(exc)}
    took = round((time.perf_counter() - started) * 1000)
    if back != body:
        return {"ok": False, "why": "read back different bytes", "ms": took}
    return {"ok": True, "ms": took}
