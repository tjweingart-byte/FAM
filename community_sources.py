"""Reddit's and X's own APIs, read-only, for `/admin/groups` (§581).

Two sources better than a web search for their platforms: Reddit's
subreddit search and about pages, and X's Communities. Both are read-only
credentials FAM holds for its own outreach, like the research keys; nothing
here can post, message, vote or follow ([no-social-posting],
[community-outreach]).

* **Reddit**: an app-only OAuth token (`client_credentials`) from
  `REDDIT_CLIENT_ID` and `REDDIT_CLIENT_SECRET`, held in memory until it
  expires, then `oauth.reddit.com`'s `/subreddits/search` and
  `/r/<name>/about`. Reddit wants a descriptive User-Agent; it names FAM's
  outreach tool and nobody's address. Reddit's terms ask commercial users for
  an agreement: holding the key is the owner's statement that FAM has one.
* **X**: `X_BEARER_TOKEN` (§321's key, read again) and X API v2's
  `/2/communities/search` and `/2/communities/<id>`. X bills per resource
  read; each request is counted, and its price is not known here, so it is
  counted unpriced, never as free.

Each returns `Found` rows - a community's id, name, description and member
count - or raises `SourceError` with a sentence. A source with no key says
so (`reddit_available`, `x_available`) and the caller falls back to the web
search. Nothing here reads a moderator, a member or a profile: the way to
the admins is the community's own (modmail, the moderators page), built in
`communities.contact_url`.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("fam.community_sources")

REDDIT_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
REDDIT_API = "https://oauth.reddit.com"
X_API = "https://api.x.com/2"
#: Reddit's rule: a unique, descriptive User-Agent. No person's address.
USER_AGENT = "server:fam-outreach:1.0 (community search for FAM's admin page)"
TIMEOUT = 15.0
#: Results asked for per search.
LIMIT = 10
X_FIELDS = "id,name,description,member_count,access,join_policy"


class SourceError(RuntimeError):
    """A source could not answer, in words."""


@dataclass
class Found:
    platform: str
    ident: str
    name: str
    description: str = ""
    members: int = 0
    privacy: str = ""
    #: Adults only, as the platform marks it: never a card.
    adult: bool = False
    #: Where the facts came from, for the card's "Found on".
    source_url: str = ""


def _key(name: str) -> str:
    import credentials

    return (credentials.active(name) or "").strip()


def _count(provider: str, ok: bool) -> None:
    try:
        import provider_usage

        provider_usage.record(provider, ok=ok)
        if ok:
            # Billed or metered per request at a rate not known here: counted
            # in the day's spend as unpriced, never as $0 (§481).
            provider_usage.spend("community outreach", f"{provider} api", usd=None)
    except Exception:  # noqa: BLE001 - counting never costs a lookup
        pass


def _get(url: str, headers: dict, params: Optional[dict] = None) -> dict:
    import httpx

    with httpx.Client(timeout=TIMEOUT) as client:
        reply = client.get(url, headers=headers, params=params)
    if reply.status_code == 404:
        raise SourceError("not found")
    if reply.status_code in (401, 403):
        raise SourceError(f"refused ({reply.status_code}): the key is wrong or lacks access")
    if reply.status_code == 429:
        raise SourceError("rate limited: try again later")
    reply.raise_for_status()
    return reply.json()


# --------------------------------------------------------------------------
# Reddit


def reddit_available() -> tuple[bool, str]:
    if not (_key("REDDIT_CLIENT_ID") and _key("REDDIT_CLIENT_SECRET")):
        return False, "REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET are not set"
    return True, "Reddit's API"


_TOKEN: dict = {"value": "", "until": 0.0}
_TOKEN_LOCK = threading.Lock()


def _reddit_token() -> str:
    """An app-only token, kept in memory until a minute before it expires."""
    import httpx

    with _TOKEN_LOCK:
        if _TOKEN["value"] and time.time() < _TOKEN["until"]:
            return _TOKEN["value"]
        with httpx.Client(timeout=TIMEOUT) as client:
            reply = client.post(REDDIT_TOKEN_URL,
                                auth=(_key("REDDIT_CLIENT_ID"), _key("REDDIT_CLIENT_SECRET")),
                                data={"grant_type": "client_credentials"},
                                headers={"User-Agent": USER_AGENT})
        if reply.status_code in (401, 403):
            raise SourceError("Reddit refused the client id and secret")
        reply.raise_for_status()
        body = reply.json()
        token = str(body.get("access_token") or "")
        if not token:
            raise SourceError("Reddit gave no token")
        _TOKEN.update(value=token,
                      until=time.time() + max(60.0, float(body.get("expires_in") or 3600) - 60))
        return token


def reset_token() -> None:
    with _TOKEN_LOCK:
        _TOKEN.update(value="", until=0.0)


def _reddit(path: str, params: Optional[dict] = None) -> dict:
    ok, why = reddit_available()
    if not ok:
        raise SourceError(why)
    try:
        body = _get(REDDIT_API + path, {"Authorization": f"Bearer {_reddit_token()}",
                                        "User-Agent": USER_AGENT}, params)
    except Exception:
        _count("reddit", False)
        raise
    _count("reddit", True)
    return body


def _subreddit(data: dict) -> Optional[Found]:
    name = str(data.get("display_name") or "").strip()
    if not name:
        return None
    kind = str(data.get("subreddit_type") or "")
    return Found(platform="reddit", ident=name.lower(),
                 name=str(data.get("title") or "").strip() or f"r/{name}",
                 description=" ".join(str(data.get("public_description") or "").split())[:600],
                 members=int(data.get("subscribers") or 0),
                 privacy="private" if kind == "private" else "public" if kind == "public" else kind,
                 adult=bool(data.get("over18")),
                 source_url=f"https://www.reddit.com/r/{name}")


def reddit_search(query: str, limit: int = LIMIT) -> list[Found]:
    body = _reddit("/subreddits/search", {"q": query, "limit": int(limit),
                                          "include_over_18": "false", "raw_json": 1})
    out = []
    for child in (body.get("data") or {}).get("children") or []:
        found = _subreddit((child or {}).get("data") or {})
        if found is not None:
            out.append(found)
    return out


def reddit_about(name: str) -> Found:
    body = _reddit(f"/r/{name}/about", {"raw_json": 1})
    found = _subreddit(body.get("data") or {})
    if found is None:
        raise SourceError(f"r/{name} has no about page")
    return found


# --------------------------------------------------------------------------
# X


def x_available() -> tuple[bool, str]:
    if not _key("X_BEARER_TOKEN"):
        return False, "X_BEARER_TOKEN is not set"
    return True, "X's API"


def _x(path: str, params: dict) -> dict:
    ok, why = x_available()
    if not ok:
        raise SourceError(why)
    try:
        body = _get(X_API + path, {"Authorization": f"Bearer {_key('X_BEARER_TOKEN')}",
                                   "User-Agent": USER_AGENT}, params)
    except Exception:
        _count("x", False)
        raise
    _count("x", True)
    if body.get("errors") and not body.get("data"):
        raise SourceError(str((body["errors"][0] or {}).get("detail") or "X answered with an error"))
    return body


def _community(data: dict) -> Optional[Found]:
    ident = str(data.get("id") or "").strip()
    if not ident.isdigit():
        return None
    access = str(data.get("access") or data.get("join_policy") or "").lower()
    return Found(platform="x", ident=ident, name=str(data.get("name") or "").strip() or ident,
                 description=" ".join(str(data.get("description") or "").split())[:600],
                 members=int(data.get("member_count") or 0),
                 privacy="public" if access in ("public", "open") else access,
                 source_url=f"https://x.com/i/communities/{ident}")


def x_search(query: str, limit: int = LIMIT) -> list[Found]:
    body = _x("/communities/search", {"query": query[:512], "max_results": max(10, int(limit)),
                                      "community.fields": X_FIELDS})
    return [f for f in (_community(d or {}) for d in body.get("data") or []) if f is not None]


def x_about(ident: str) -> Found:
    body = _x(f"/communities/{ident}", {"community.fields": X_FIELDS})
    found = _community(body.get("data") or {})
    if found is None:
        raise SourceError(f"X has no community {ident}")
    return found


# --------------------------------------------------------------------------


def report() -> dict:
    """What each API can do now, for the page."""
    return {name: {"available": ok, "detail": why}
            for name, (ok, why) in (("reddit", reddit_available()), ("x", x_available()))}
