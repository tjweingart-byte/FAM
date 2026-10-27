"""§320: a key set again after the pool was rebuilt without it is seen.

`credentials.pool` memoises the keys it read, and keeps the memo when the only
thing that moved is the value it published itself (a failover). A rebuild that
no longer saw that value kept remembering it, so the same key set again later
read as its own failover and the memo built without it - an empty pool - was
kept with the key in the environment.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import credentials  # noqa: E402

NAME = "FAM_TEST_MEMO_KEY"


def _forget():
    for table in (credentials._POOL, credentials._SOURCED,
                  credentials._CURSOR, credentials._PUBLISHED):
        table.pop(NAME, None)


def test_a_key_set_again_after_a_rebuild_without_it_is_in_the_pool(monkeypatch):
    _forget()
    monkeypatch.delenv(NAME + "S", raising=False)
    try:
        monkeypatch.setenv(NAME, "k1")
        assert credentials.pool(NAME) == ["k1"]
        credentials._publish(NAME)
        monkeypatch.delenv(NAME)
        assert credentials.pool(NAME) == [], "the rebuild still saw the key"
        monkeypatch.setenv(NAME, "k1")
        assert credentials.pool(NAME) == ["k1"], \
            "an empty memo was kept with the key in the environment"
    finally:
        _forget()


def test_our_own_failover_still_keeps_the_memo(monkeypatch):
    _forget()
    monkeypatch.delenv(NAME, raising=False)
    try:
        monkeypatch.setenv(NAME + "S", "k1,k2")
        assert credentials.pool(NAME) == ["k1", "k2"]
        credentials._CURSOR[NAME] = 1
        credentials._publish(NAME)            # writes k2 into NAME
        assert os.environ[NAME] == "k2"
        assert credentials.pool(NAME) == ["k1", "k2"]
        assert credentials._CURSOR[NAME] == 1, "a failover reset the cursor"
    finally:
        os.environ.pop(NAME, None)
        _forget()
