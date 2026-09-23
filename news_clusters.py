"""Headlines into stories: which articles are about the same thing.

The Trending rail used to be fifteen fixed GDELT *themes* ranked by volume -
"inflation", "sport", "the stock market" - so what it called trending was a
list of subjects that are always being written about, and the row read the
same every day. That was named in `gdelt.py` as the honest limitation of a
query-driven index: DOC tells you how much coverage a query you name is
getting; it does not hand you the stories. §135 closes it.

What does hand you the stories is the articles themselves. A sweep reads a
few hundred recent headlines - worldwide and from each region's press - and
this module groups the ones that share their words. A group that several
outlets are running is a story, and **how many outlets are running it is how
popular it is**: that is the measurement the whole Trending row is ranked on.

Deliberately plain, and deliberately no model:

* **Words, not embeddings.** Two headlines about one event share its proper
  nouns - the people, places, teams and companies - and that is enough to
  group them. An embedding would group "the Fed held rates" with "the ECB
  held rates", which is exactly the merge a trending row must not make.
* **Greedy and deterministic.** The same articles in the same order make the
  same groups, so a sweep that sees the same news produces the same stories
  and identity downstream does not churn.
* **Popularity is outlets, not articles.** One outlet syndicating a wire
  story across twelve regional sites is not twelve outlets deciding a story
  matters; hosts are counted once each.

It runs once per sweep for every listener, on text the sweep already fetched,
so it costs nothing per listener and nothing per request.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, Optional

#: Words that say nothing about which story a headline is. English only - a
#: headline in another language keeps all its words, which makes it harder to
#: group and never wrongly grouped, the cheaper of the two mistakes.
STOPWORDS = frozenset("""
a about above after again against ago all also am amid an and any are around
as at back be because been before being below between both but by can could
day days did do does doing down during each even ever every few first for
from further get gets got had has have having he her here hers him his how i
if in into is it its just last late latest like live make makes man many may
me might more most much must my near new news next no nor not now of off on
once one only or other our out over own per said same say says she should
since so some still such take takes than that the their them then there these
they this those three through to today too top two under until up update
updates us very via video watch was way we week weeks were what when where
which while who whom why will with without would year years yesterday you
your report reports reported breaking exclusive analysis opinion explainer
photos pictures podcast review what's here's it's says said told tells
""".split())

_WORD = re.compile(r"[^\W_]+", re.UNICODE)

#: Two headlines share a story when they share at least this many salient
#: words that the group already holds in common.
MIN_SHARED = 2
#: What "held in common" means: a word at least this share of the group uses.
CORE_SHARE = 0.34
#: A group needs this many distinct outlets to be a story rather than one
#: publisher's scoop. One outlet is not a trend.
MIN_OUTLETS = 2


def _stem(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def tokens(text: str) -> frozenset:
    """The words of a headline that could say which story it is."""
    out = set()
    for raw in _WORD.findall((text or "").lower().replace("'s", "")):
        if raw.isdigit() or len(raw) < 3 or raw in STOPWORDS:
            continue
        out.add(_stem(raw))
    return frozenset(out)


def names(text: str) -> frozenset:
    """The words a headline capitalises: its people, places and organisations.

    What actually distinguishes one story from another. "Fed holds rates
    steady" and "ECB holds rates steady" share three words and no names, and
    they are two stories; a group therefore has to share a name as well as
    words. A headline in title case capitalises everything, which makes every
    word a name and leaves the word rule to decide - the old behaviour, and
    the safe direction to fail in.
    """
    out = set()
    for raw in _WORD.findall((text or "").replace("'s", "")):
        low = raw.lower()
        if len(raw) < 2 or low in STOPWORDS or raw.isdigit():
            continue
        if raw[0].isupper() or (raw.isupper() and len(raw) > 1):
            out.add(_stem(low))
    return frozenset(out)


def host(url: str) -> str:
    text = (url or "").split("//", 1)[-1].split("/", 1)[0].lower()
    return text[4:] if text.startswith("www.") else text


@dataclass
class Cluster:
    """One story: the headlines that are about it, and who is running them."""

    titles: list = field(default_factory=list)
    urls: list = field(default_factory=list)
    hosts: set = field(default_factory=set)
    countries: list = field(default_factory=list)
    #: Which sweep scopes found it - "world", or a region key. A story found
    #: only in one region's press is that region's story even when the
    #: publishers' countries are missing from the rows.
    scopes: Counter = field(default_factory=Counter)
    dates: list = field(default_factory=list)
    words: Counter = field(default_factory=Counter)
    named: Counter = field(default_factory=Counter)
    size: int = 0

    @property
    def outlets(self) -> int:
        return len(self.hosts)

    def core(self) -> set:
        """The words this group holds in common."""
        floor = max(1.0, CORE_SHARE * self.size)
        return {w for w, n in self.words.items() if n >= floor}

    def keywords(self, top: int = 6) -> tuple:
        """Its most shared words, most shared first. The story's fingerprint."""
        ranked = sorted(((n, w) for w, n in self.words.items() if n >= 2),
                        key=lambda row: (-row[0], row[1]))
        if not ranked:
            ranked = sorted(((n, w) for w, n in self.words.items()),
                            key=lambda row: (-row[0], row[1]))
        return tuple(w for _n, w in ranked[:top])

    def headline(self) -> str:
        """The member headline that says most of what the group has in common.

        Shortest among the best, because a shorter headline carrying the same
        shared words is the one with the least editorialising in it.
        """
        core = self.core() or set(self.keywords())
        best = None
        for title in self.titles:
            score = len(tokens(title) & core)
            key = (-score, len(title), title)
            if best is None or key < best[0]:
                best = (key, title)
        return best[1] if best else ""

    def latest(self) -> str:
        return max(self.dates) if self.dates else ""

    def main_scope(self) -> str:
        """The scope that found most of it; "world" wins ties."""
        if not self.scopes:
            return ""
        return sorted(self.scopes.items(),
                      key=lambda kv: (-kv[1], kv[0] != "world", kv[0]))[0][0]


def cluster(articles: Iterable, scope_of=None) -> list:
    """Group articles into stories. Largest first, singletons dropped.

    `articles` are anything with `title`, `url`, and optionally `country` and
    `published_date` - the Exa-shaped results `gdelt.parse_articles` returns.
    `scope_of(article)` says which sweep found it, when the caller knows.
    Duplicate URLs are read once.
    """
    groups: list = []
    seen: set = set()
    for article in articles:
        url = str(getattr(article, "url", "") or "")
        title = str(getattr(article, "title", "") or "").strip()
        if not title or url in seen:
            continue
        seen.add(url)
        words = tokens(title)
        if len(words) < MIN_SHARED:
            continue
        called = names(title) & words

        best: Optional[Cluster] = None
        best_shared = 0
        for group in groups:
            core = group.core()
            shared = len(words & core)
            # A shared name as well as shared words - see `names` - unless
            # this headline names nobody, when words are all there is.
            if called and not (called & core & set(group.named)):
                continue
            if shared >= MIN_SHARED and (
                    shared > best_shared
                    or (shared == best_shared and best is not None
                        and group.size > best.size)):
                best, best_shared = group, shared
        if best is None:
            best = Cluster()
            groups.append(best)

        best.titles.append(title)
        best.urls.append(url)
        best.hosts.add(host(url))
        country = str(getattr(article, "country", "") or "")
        if country:
            best.countries.append(country)
        date = str(getattr(article, "published_date", "") or "")
        if date:
            best.dates.append(date)
        if scope_of is not None:
            scope = scope_of(article)
            if scope:
                best.scopes[scope] += 1
        best.words.update(words)
        best.named.update(called)
        best.size += 1

    stories = [g for g in groups if g.outlets >= MIN_OUTLETS]
    stories.sort(key=lambda g: (-g.outlets, -g.size, g.headline()))
    return stories


def overlap(a: Iterable, b: Iterable) -> int:
    """How many salient words two fingerprints share."""
    return len(set(a) & set(b))


def same_story(a: Iterable, b: Iterable) -> bool:
    """Whether two fingerprints are one story seen on two sweeps.

    Half of the smaller fingerprint, and never fewer than two words: a
    story's leading headline moves between sweeps while its people and
    places do not.
    """
    a, b = set(a), set(b)
    if not a or not b:
        return False
    need = max(MIN_SHARED, (min(len(a), len(b)) + 1) // 2)
    return len(a & b) >= need
