"""Who actually draws the picture.

One interface, three implementations, and the interface is the point: the
provider is the part of this feature most likely to be replaced, because image
models move faster than anything else in the stack. Everything downstream of
here - the line processor, the validator, the store, the player - works on a
raster and knows nothing about who produced it.

    VisualImageProvider.generate(brief, attempt, references) -> GeneratedImage

* **`openai`** - the production provider. `gpt-image-1` is the highest-quality
  square-image model this project can reach with one API key and no account
  setup beyond billing, and its line work at high quality is the closest
  available thing to the target. Chosen on quality rather than price: a
  premium illustration is the feature, and the cheap models produce exactly
  the "generic AI illustration" the brief rules out.
* **`synthetic`** - draws a real continuous line locally, from the brief, with
  no network and no key. It exists so that the whole pipeline - director,
  processor, validator, store, player - can be run and seen end to end on a
  machine with no image credential, and so the tests exercise real geometry
  rather than a fixture.
* **`none`** - the default when nothing is configured. Refuses, loudly and in
  a sentence someone can act on.

**Synthetic is never a fallback.** It is selected explicitly or not at all, and
that is this project's oldest rule applied to pixels: an app that quietly looks
worse than intended is the failure FAM has lost the most time to, and a
placeholder that substitutes itself for the real thing when a key is missing is
exactly how that happens. With no key, an episode has no illustration and every
surface says so - the same choice as the placeholder tone standing in for a
voice rather than a lesser voice standing in for Chatterbox.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import time
from dataclasses import dataclass, field
from typing import Protocol

import httpx

import credentials
import visual_style
from config import settings

log = logging.getLogger(__name__)


class VisualProviderError(RuntimeError):
    """Image generation failed, phrased so it can be acted on."""


class VisualProviderUnconfigured(VisualProviderError):
    """No provider is configured. Distinct from a failure: nothing is broken,
    something has simply not been set up, and the two want different sentences
    on `/api/health`."""


@dataclass
class GeneratedImage:
    """The source artwork, and everything worth knowing about how it got here.

    `data` is raster bytes - normally PNG. It is **not** the final asset: the
    line processor turns it into the canonical vector, and the vector is what
    the thumbnail and the player are both rendered from. Keeping the source is
    for diagnosis, not for display; a listener never sees these pixels.
    """

    data: bytes
    media_type: str = "image/png"
    provider: str = ""
    model: str = ""
    #: Priced, not billed. The provider's published rate for this request
    #: shape - `metering.py`'s distinction, kept because a number that does not
    #: say where it came from is a number that gets quoted as an invoice.
    cost_usd: float = 0.0
    latency_ms: int = 0
    prompt: str = ""
    references: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "cost_usd": round(self.cost_usd, 5),
            "cost_basis": "priced from the provider's published rate",
            "latency_ms": self.latency_ms,
            "bytes": len(self.data),
            "references": list(self.references),
        }


class VisualImageProvider(Protocol):
    name: str
    model: str

    def configured(self) -> tuple[bool, str]: ...

    async def generate(self, brief, *, attempt: int = 1,
                       references=()) -> GeneratedImage: ...


# --------------------------------------------------------------------------
# OpenAI
# --------------------------------------------------------------------------
#: Published rate for one high-quality 1024x1024 `gpt-image-1` image, in USD.
#: Priced rather than billed - the API does not return a cost, and a number
#: invented here would be quoted as one. Update it when the rate moves; it is
#: used for the daily ceiling and for the ledger, not for a bill.
OPENAI_IMAGE_PRICE = {
    ("gpt-image-1", "high"): 0.167,
    ("gpt-image-1", "medium"): 0.042,
    ("gpt-image-1", "low"): 0.011,
}
OPENAI_BASE = "https://api.openai.com/v1"


class OpenAIImageProvider:
    """`gpt-image-1`, through the images API.

    Two endpoints, one decision: with approved FAM references on the machine
    the request goes to `/images/edits`, which is how `gpt-image-1` is shown
    reference imagery; with none it goes to `/images/generations`, which is the
    same model with the style described in words only. That is the one place
    `visual_references/` changes anything, and it is why filling that folder is
    the strongest lever on how these look.
    """

    name = "openai"

    def __init__(self, api_key: str = "", model: str = "",
                 quality: str = "", size: str = "",
                 timeout: float = 0.0) -> None:
        self.api_key = api_key or _image_key()
        self.model = model or settings.visual_image_model
        self.quality = quality or settings.visual_image_quality
        self.size = size or settings.visual_image_size
        self.timeout = timeout or settings.visual_image_timeout_seconds

    def configured(self) -> tuple[bool, str]:
        if not self.api_key:
            return False, (
                "VISUAL_IMAGE_API_KEY is not set (OPENAI_API_KEY is accepted "
                "too). Episodes will have no illustration until it is."
            )
        return True, ""

    def price(self) -> float:
        return OPENAI_IMAGE_PRICE.get((self.model, self.quality), 0.0)

    async def generate(self, brief, *, attempt: int = 1,
                       references=()) -> GeneratedImage:
        ready, why = self.configured()
        if not ready:
            raise VisualProviderUnconfigured(why)

        prompt = visual_style.image_prompt(brief, attempt)
        started = time.monotonic()
        references = list(references or [])
        try:
            if references:
                payload = await self._edit(prompt, references)
            else:
                payload = await self._generate(prompt)
        except httpx.TimeoutException as exc:
            raise VisualProviderError(
                f"{self.model} did not answer within {self.timeout:g}s"
            ) from exc
        except httpx.HTTPError as exc:
            raise VisualProviderError(f"{self.model} could not be reached: {exc}") from exc

        data = _first_image(payload, self.model)
        return GeneratedImage(
            data=data,
            media_type="image/png",
            provider=self.name,
            model=self.model,
            cost_usd=self.price(),
            latency_ms=int((time.monotonic() - started) * 1000),
            prompt=prompt,
            references=[ref.name for ref in references],
        )

    # -- the two shapes of request -----------------------------------------
    async def _generate(self, prompt: str) -> dict:
        body = {
            "model": self.model,
            "prompt": prompt,
            "size": self.size,
            "quality": self.quality,
            "n": 1,
            # A transparent or auto background would let the ivory come from
            # whatever is behind the image, and the ivory is part of the
            # product. Opaque, and the colour is asked for in the prompt.
            "background": "opaque",
            "output_format": "png",
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{OPENAI_BASE}/images/generations",
                                         headers=self._headers(), json=body)
        return _checked(response, self.model)

    async def _edit(self, prompt: str, references) -> dict:
        files = []
        for ref in references:
            files.append(("image[]", (ref.name, base64.b64decode(ref.data_b64),
                                      ref.media_type)))
        data = {
            "model": self.model,
            "prompt": (
                "Draw a NEW illustration in exactly the style of the reference "
                "images - the same line weight, the same ivory ground, the "
                "same restraint and the same use of empty space. Do not copy "
                "their subject.\n\n" + prompt
            ),
            "size": self.size,
            "quality": self.quality,
            "n": "1",
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{OPENAI_BASE}/images/edits",
                                         headers=self._headers(), data=data,
                                         files=files)
        return _checked(response, self.model)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"}


def _checked(response: httpx.Response, model: str) -> dict:
    """The response, or the provider's own words about why there isn't one.

    The body is read on an error rather than only the status code: an image
    API's refusals - content policy, a billing hold, an unknown model - all
    arrive as 400s that are indistinguishable from each other until you read
    what it said, and "400 from the image provider" is not something anybody
    can act on.
    """
    if response.status_code >= 400:
        detail = ""
        try:
            detail = str(response.json().get("error", {}).get("message", "")).strip()
        except Exception:  # noqa: BLE001 - a non-JSON error body is still an error
            detail = response.text[:300].strip()
        raise VisualProviderError(
            f"{model} refused the request ({response.status_code})"
            + (f": {detail}" if detail else "")
        )
    try:
        return response.json()
    except ValueError as exc:
        raise VisualProviderError(f"{model} returned a body that is not JSON") from exc


def _first_image(payload: dict, model: str) -> bytes:
    items = payload.get("data") or []
    if not items:
        raise VisualProviderError(f"{model} returned no image")
    first = items[0] or {}
    encoded = first.get("b64_json")
    if not encoded:
        # The URL form is not followed. A second fetch is a second thing that
        # can fail on a path that is already speculative, and every model this
        # provider targets returns base64 for image generation.
        raise VisualProviderError(
            f"{model} returned a URL rather than image data; FAM asks for "
            "base64 so that generation is one round trip"
        )
    try:
        return base64.b64decode(encoded)
    except Exception as exc:  # noqa: BLE001
        raise VisualProviderError(f"{model} returned unreadable image data") from exc


def _image_key() -> str:
    """The image credential, from the credential chain.

    `VISUAL_IMAGE_API_KEY` first so that the image provider can be given its
    own key - a different account, a different budget, a different blast radius
    from the key that writes the episodes - and `OPENAI_API_KEY` after it,
    because that is the name the key arrives under from OpenAI and asking
    somebody to rename it is a step that gets skipped.
    """
    for name in ("VISUAL_IMAGE_API_KEY", "OPENAI_API_KEY"):
        value = (credentials.active(name) or "").strip()
        if value:
            return value
    return ""


def image_key_source() -> str:
    """Which variable the image key came from, for `/api/health`."""
    for name in ("VISUAL_IMAGE_API_KEY", "OPENAI_API_KEY"):
        if (credentials.active(name) or "").strip():
            return name
    return ""


# --------------------------------------------------------------------------
# Synthetic
# --------------------------------------------------------------------------
class SyntheticProvider:
    """A real continuous line, drawn locally from the brief.

    What it is for: seeing the whole feature work without an image credential.
    The curve it draws is genuinely one unbroken path with loops and crossings,
    so the skeletoniser, the graph, the traversal, the validator, the store and
    the player all do their real work on it. What it is *not* for is shipping:
    it is abstract, it is not art-directed, and it says so everywhere it
    appears - `provider: "synthetic"` on the record, on the API, on
    `/api/health`, and a label on the tile.

    It is deterministic in the brief, so the same episode always draws the same
    figure. A placeholder that changed every time would be a placeholder nobody
    could recognise as one.
    """

    name = "synthetic"
    model = "fam-lissajous-1"

    def configured(self) -> tuple[bool, str]:
        return True, ""

    async def generate(self, brief, *, attempt: int = 1,
                       references=()) -> GeneratedImage:
        import line_processor  # local: numpy is only needed on this path

        started = time.monotonic()
        seed = _seed_for(brief)
        # Attempt 3 asks for something simpler, and the synthetic provider can
        # honour that literally - fewer harmonics is a plainer figure.
        complexity = {"low": 2, "medium": 3, "high": 4}.get(
            getattr(brief, "complexity", "medium"), 3)
        if attempt >= 3:
            complexity = 2
        points = await asyncio.to_thread(_figure, seed, complexity)
        data = await asyncio.to_thread(
            line_processor.rasterise_polyline, points,
            settings.visual_source_pixels, 2.6)
        return GeneratedImage(
            data=data,
            media_type="image/png",
            provider=self.name,
            model=self.model,
            cost_usd=0.0,
            latency_ms=int((time.monotonic() - started) * 1000),
            prompt=visual_style.image_prompt(brief, attempt),
        )


def _seed_for(brief) -> int:
    import hashlib

    text = "|".join(str(getattr(brief, name, "")) for name in
                    ("subject", "primary_form", "visual_metaphor"))
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], 16)


def _figure(seed: int, harmonics: int) -> list:
    """A closed harmonic curve - one line, no lifts, a few crossings."""
    import math
    import random

    rng = random.Random(seed)
    terms = []
    for _ in range(harmonics):
        terms.append((
            rng.uniform(0.12, 0.42),          # amplitude, as a share of radius
            rng.choice((2, 3, 4, 5, 6, 7)),   # frequency
            rng.uniform(0, math.tau),         # phase
        ))
    points = []
    steps = 1800
    for i in range(steps + 1):
        t = i / steps * math.tau
        r = 1.0
        for amp, freq, phase in terms:
            r += amp * math.sin(freq * t + phase)
        points.append((r * math.cos(t), r * math.sin(t)))
    # Fitted to the canvas rather than trusted to land on it. A harmonic sum
    # can put the radius anywhere, and a figure drawn partly off the edge comes
    # back from the line processor as two disconnected pieces - a gap the
    # provider made and the processor is blamed for.
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    span = max(max(xs) - min(xs), max(ys) - min(ys)) or 1.0
    scale = 0.84 / span
    ox = 0.5 - (min(xs) + max(xs)) / 2 * scale
    oy = 0.5 - (min(ys) + max(ys)) / 2 * scale
    return [(x * scale + ox, y * scale + oy) for x, y in points]


# --------------------------------------------------------------------------
# None
# --------------------------------------------------------------------------
class NoProvider:
    """No image provider. Says exactly what to do about it."""

    name = "none"
    model = ""

    def configured(self) -> tuple[bool, str]:
        return False, (
            "No image provider is configured. Set VISUAL_IMAGE_PROVIDER=openai "
            "and supply VISUAL_IMAGE_API_KEY (or OPENAI_API_KEY) to draw real "
            "FAM illustrations, or VISUAL_IMAGE_PROVIDER=synthetic to see the "
            "pipeline run with clearly-labelled placeholder line art."
        )

    async def generate(self, brief, *, attempt: int = 1,
                       references=()) -> GeneratedImage:
        raise VisualProviderUnconfigured(self.configured()[1])


PROVIDERS = {
    "openai": OpenAIImageProvider,
    "synthetic": SyntheticProvider,
    "none": NoProvider,
}


def build_provider(name: str = "") -> VisualImageProvider:
    """The configured provider, or `NoProvider`.

    An unknown name is `none` plus a warning rather than a crash: a typo in an
    environment variable must not stop the server from starting, and it must
    not quietly select something either.
    """
    chosen = (name or settings.visual_image_provider or "none").strip().lower()
    factory = PROVIDERS.get(chosen)
    if factory is None:
        log.warning("VISUAL_IMAGE_PROVIDER=%r is not one of %s; no "
                    "illustrations will be generated", chosen,
                    ", ".join(sorted(PROVIDERS)))
        return NoProvider()
    # `openai` with no key resolves to itself and reports unconfigured, rather
    # than falling through to `synthetic`. See the module docstring: a silent
    # substitution is the failure this project keeps paying for.
    return factory()


def report() -> dict:
    """What `/api/health` says about who is drawing.

    `configured` is the honest question - "a provider is named" and "a provider
    can run" are different claims, and only one of them means a listener will
    see a picture.
    """
    provider = build_provider()
    ready, why = provider.configured()
    return {
        "provider": provider.name,
        "model": getattr(provider, "model", ""),
        "configured": ready,
        "reason": why,
        "key_source": image_key_source() if provider.name == "openai" else "",
        "quality": getattr(provider, "quality", ""),
        "size": getattr(provider, "size", ""),
        "priced_per_image_usd": provider.price() if hasattr(provider, "price") else 0.0,
        "placeholder": provider.name == "synthetic",
        "placeholder_note": (
            "synthetic line art is standing in for a real image model. It is "
            "not FAM artwork and is labelled as such everywhere it appears."
        ) if provider.name == "synthetic" else "",
    }
