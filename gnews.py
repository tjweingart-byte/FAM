"""GNews (gnews.io): the trending bank's news source.

What it is for
--------------
One job: telling `trending_bank` what the world's press is leading with, twice
a day. Not evidence for an episode - an episode is still researched on Exa and
GDELT exactly as before, and a tile's headline is never read to the writer as
fact. GNews decides *which* stories are trending; research decides what is
true about them.

Why GNews rather than GDELT for this (§139)
-------------------------------------------
GDELT is keyless and asks for one request every five seconds per address. The
story sweep made about thirty-two at a time from Render's shared addresses,
inside a forty-five second ceiling, so it timed out and Trending said so. The
fix for a free research service that asks to be treated gently is not to ask
it harder; it is a keyed, rate-documented feed whose "top headlines" endpoint
is already the answer to "what is the press leading with".

Two calls, both documented at https://gnews.io/docs/v4:

* `top-headlines` - by `category` (worldwide) and by `country`. What the
  press is leading with. Position in the feed is a real signal.
* `search` - for a candidate story's words, over the last twelve hours.
  `totalArticles` is how widely it is being run: the popularity an edition
  ranks on, in one request per candidate rather than a volume timeline.

Rules
-----
* **The key never reaches a log.** GNews takes it as a query parameter, and
  httpx logs every request URL at INFO. `_Redact` is installed on the httpx
  logger at import and blanks `apikey=` wherever it appears; errors raised
  from here carry the endpoint name, never the URL.
* **Raises on failure, returns `[]` on silence.** "GNews answered with
  nothing" and "GNews did not answer" are different sentences on
  `/api/health`, and only the second is a reason to look at the plan.
* **Counted.** Every request is charged to the bank's per-day ledger before it
  is made (`trending_bank.BankStore.spend`), so a rebuild loop cannot spend a
  plan's allowance, and a refused spend is a `QuotaSpent`, not a request.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import httpx

from config import settings

log = logging.getLogger(__name__)

API = "https://gnews.io/api/v4"
#: The categories `top-headlines` accepts. Anything else is dropped at the
#: boundary with a warning rather than sent and refused.
CATEGORIES = ("general", "world", "nation", "business", "technology",
              "entertainment", "sports", "science", "health")


class GNewsError(RuntimeError):
    """GNews did not answer usefully. The message never carries the key.

    `status` is GNews's HTTP status, or 0 when none came back. `fatal` says
    the next request would be refused too - a refused key (401), a spent or
    insufficient plan (403), a rate limit (429) - so a caller should stop
    asking rather than charge more requests to the day.
    """

    FATAL = (401, 403, 429)

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status

    @property
    def fatal(self) -> bool:
        return self.status in self.FATAL


class QuotaSpent(GNewsError):
    """The app's own daily ceiling refused the request before it was made."""

    @property
    def fatal(self) -> bool:
        return True


class _Redact(logging.Filter):
    """Blank the key out of any log line that carries a URL with it."""

    PATTERN = re.compile(r"(apikey=)[^&\s'\"]+", re.IGNORECASE)

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - never lose a log line to a filter
            return True
        if "apikey=" in message.lower():
            record.msg = self.PATTERN.sub(r"\1[redacted]", message)
            record.args = ()
        return True


for _name in ("httpx", "httpcore"):
    _logger = logging.getLogger(_name)
    if not any(isinstance(f, _Redact) for f in _logger.filters):
        _logger.addFilter(_Redact())


@dataclass(frozen=True)
class Article:
    """One GNews article, in the shape `news_clusters.cluster` reads."""

    title: str
    url: str
    description: str = ""
    published_date: str = ""
    source: str = ""
    #: The publisher's country as GNews reports it, lower-case, or "". Folded
    #: through `stories.normalise_country` by whoever reads it.
    country: str = ""
    #: Which feed found it - "category:world", "country:us", "search".
    feed: str = ""
    #: Its position in that feed, 0 first. The press's own ordering.
    rank: int = 0


@dataclass
class SearchResult:
    total: int = 0
    articles: list = field(default_factory=list)


def key() -> str:
    return (settings.gnews_key or "").strip()


def available() -> tuple[bool, str]:
    """Whether GNews can be asked. Cheap; no network."""
    if not key():
        return False, "GNEWS_KEY is not set"
    return True, "GNEWS_KEY present (not verified from this machine)"


def categories() -> list:
    wanted = [c.strip().lower() for c in settings.gnews_categories.split(",")
              if c.strip()]
    bad = [c for c in wanted if c not in CATEGORIES]
    if bad:
        log.warning("gnews: ignoring unknown categories %s (GNews accepts %s)",
                    ", ".join(bad), ", ".join(CATEGORIES))
    return [c for c in wanted if c in CATEGORIES]


def countries() -> list:
    return [c.strip().lower() for c in settings.gnews_countries.split(",")
            if len(c.strip()) == 2]


def parse(payload: dict, feed: str = "") -> SearchResult:
    """A GNews v4 response as articles. Tolerant: a malformed row is skipped."""
    out: list = []
    rows = (payload or {}).get("articles") or []
    for position, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or "").strip()
        title = str(row.get("title") or "").strip()
        if not url or not title:
            continue
        source = row.get("source") if isinstance(row.get("source"), dict) else {}
        out.append(Article(
            title=title,
            url=url,
            description=str(row.get("description") or "").strip(),
            published_date=str(row.get("publishedAt") or "").strip(),
            source=str(source.get("name") or "").strip(),
            country=str(source.get("country") or "").strip().lower(),
            feed=feed,
            rank=position,
        ))
    try:
        total = int((payload or {}).get("totalArticles") or 0)
    except (TypeError, ValueError):
        total = 0
    return SearchResult(total=max(total, len(out)), articles=out)


class Client:
    """Paced, counted access to GNews. One per edition build.

    `spend` is called before every request with the endpoint name and must
    return True for the request to be made - the bank's ledger. `gap` is the
    pause between two requests; the free plan allows one a second.
    """

    def __init__(self, spend: Optional[Callable[[str], bool]] = None,
                 transport: Optional[httpx.AsyncBaseTransport] = None,
                 gap: Optional[float] = None,
                 timeout: Optional[float] = None) -> None:
        self._spend = spend or (lambda _endpoint: True)
        self._transport = transport
        self._gap = settings.gnews_request_gap_seconds if gap is None else gap
        self._timeout = (settings.gnews_timeout_seconds if timeout is None
                         else timeout)
        self._last = 0.0
        self.requests = 0

    async def _get(self, endpoint: str, params: dict) -> dict:
        if not key():
            raise GNewsError("GNEWS_KEY is not set")
        if not self._spend(endpoint):
            raise QuotaSpent(
                f"the daily GNews ceiling (GNEWS_DAILY_REQUESTS="
                f"{settings.gnews_daily_requests}) is spent")
        wait = self._gap - (time.monotonic() - self._last)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last = time.monotonic()
        self.requests += 1
        query = {**params, "apikey": key()}
        try:
            async with httpx.AsyncClient(timeout=self._timeout,
                                         transport=self._transport) as client:
                response = await client.get(f"{API}/{endpoint}", params=query)
        except httpx.TimeoutException:
            raise GNewsError(f"GNews {endpoint} timed out after "
                             f"{self._timeout:.0f}s") from None
        except httpx.HTTPError as exc:
            raise GNewsError(f"GNews {endpoint} could not be reached: "
                             f"{type(exc).__name__}") from None
        finally:
            self._last = time.monotonic()
        if response.status_code != 200:
            raise GNewsError(_refusal(endpoint, response),
                             status=response.status_code)
        try:
            return response.json()
        except ValueError:
            raise GNewsError(f"GNews {endpoint} answered with something that "
                             "is not JSON") from None

    async def top_headlines(self, category: str = "", country: str = "",
                            limit: int = 0) -> list:
        params = {"lang": settings.gnews_lang or "en",
                  "max": str(max(1, min(100, limit or settings.gnews_max_articles)))}
        if category:
            params["category"] = category
        if country:
            params["country"] = country
        feed = f"category:{category}" if category else f"country:{country}"
        if category and country:
            feed = f"{category}:{country}"
        return parse(await self._get("top-headlines", params), feed).articles

    async def search(self, words: str, since_iso: str = "",
                     limit: int = 10) -> SearchResult:
        params = {"q": words, "lang": settings.gnews_lang or "en",
                  "max": str(max(1, min(100, limit)))}
        if since_iso:
            params["from"] = since_iso
        return parse(await self._get("search", params), "search")


def _refusal(endpoint: str, response: httpx.Response) -> str:
    """What went wrong, in words that name the fix. Never the URL."""
    detail = ""
    try:
        body = response.json()
        errors = body.get("errors") if isinstance(body, dict) else None
        if isinstance(errors, list):
            detail = "; ".join(str(e) for e in errors)[:200]
        elif isinstance(errors, dict):
            detail = "; ".join(f"{k}: {v}" for k, v in errors.items())[:200]
    except ValueError:
        pass
    status = response.status_code
    reason = {
        401: "GNEWS_KEY was refused",
        403: "GNews refused the request - the daily quota is spent or the "
             "plan does not include this",
        429: "GNews is rate-limiting this key",
    }.get(status, f"GNews answered {status}")
    return f"{reason} ({endpoint})" + (f": {detail}" if detail else "")


def _ledger():
    """The bank's daily ledger, so a check is counted like any request."""
    import trending_bank

    return lambda _endpoint: trending_bank.store().spend(
        trending_bank.GNEWS, settings.gnews_daily_requests)


async def verify() -> tuple[bool, str]:
    """A real request (§52). One top-headline; never on a request path."""
    ok, why = available()
    if not ok:
        return False, why
    try:
        rows = await Client(spend=_ledger()).top_headlines(category="general",
                                                            limit=1)
    except GNewsError as exc:
        return False, str(exc)
    if not rows:
        return False, "GNews answered but returned no articles"
    return True, f"GNews answered with {len(rows)} article(s)"
