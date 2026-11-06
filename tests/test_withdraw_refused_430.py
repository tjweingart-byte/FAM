"""Withdrawing what the archive kept from a refused provider (§430): a list
first, applied only with its SHA-256, reversible until a 30-day purge."""
from __future__ import annotations

import dataclasses
import json
import sys
import time
from datetime import datetime, timezone

import pytest

import history_archive as ha

sys.path.insert(0, "tools")
import withdraw_refused as wr  # noqa: E402

NOW = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)


@pytest.fixture
def archive(monkeypatch, tmp_path):
    ha.reset()
    monkeypatch.setattr(ha, "settings", dataclasses.replace(
        ha.settings, archive=True, archive_backend="sqlite"))
    monkeypatch.setattr(ha, "_embed", lambda text: ([1.0] + [0.0] * (ha.DIMS - 1),
                                                    f"onnx:{ha.DIMS}"))
    store = ha.SQLiteBackend(str(tmp_path / "archive.db"))
    ha.use(store)
    for source, entity in (("API-Sports", "Arsenal"), ("Finnhub", "NVDA"),
                           ("Tiingo", "AAPL"), ("BALLDONTLIE", "Eagles")):
        ha.add_fact(ha._live_fact({"source": source, "entity": entity,
                                   "as_of": "2026-10-20T20:00:00Z", "domain": "sports",
                                   "lines": [f"{entity} line."]}, entity, "sports"))
        store.add_reading(ha.Reading(subject=entity, metric="score", as_of=NOW,
                                     text=f"{entity} 1-0.", source=source,
                                     domain="sports"), NOW.timestamp())
    yield store
    ha.reset()


def _active_hosts(store):
    return {r["provider"] for r in store.provider_rows(
        ("api-sports", "finnhub", "tiingo", "balldontlie"))}


def test_the_list_names_only_the_refused_two(archive):
    found = wr.listing(archive)
    assert {(r["store"], r["provider"]) for r in found["rows"]} == {
        ("facts", "API-Sports"), ("facts", "Finnhub"),
        ("live_series", "API-Sports"), ("live_series", "Finnhub")}
    assert found["counts"] == {"facts / API-Sports": 1, "facts / Finnhub": 1,
                               "live_series / API-Sports": 1, "live_series / Finnhub": 1}


def test_the_list_changes_nothing_and_names_its_approval(archive, tmp_path):
    digest = wr.write_list(wr.listing(archive), tmp_path / "w.json")
    assert len(archive.provider_rows(wr.PROVIDERS)) == 4
    md = (tmp_path / "w.md").read_text()
    assert "**4 records**" in md and digest in md and "Arsenal" in md


def test_any_other_approval_or_an_edited_list_is_refused(archive, tmp_path):
    path = tmp_path / "w.json"
    digest = wr.write_list(wr.listing(archive), path)
    # One character off - never the same one (§483: "0" alone was the right
    # digest whenever the digest ended in 0, one run in sixteen).
    off = digest[:-1] + ("1" if digest[-1] == "0" else "0")
    for wrong in ("", "yes", off):
        assert wr.apply(archive, path, wrong)["refused"]
    data = json.loads(path.read_text())
    data["rows"].append({"store": "facts", "id": "x", "provider": "Tiingo"})
    path.write_text(json.dumps(data))
    assert wr.apply(archive, path, digest)["refused"]
    assert len(archive.provider_rows(wr.PROVIDERS)) == 4


def test_withdraw_restore_and_purge(archive, tmp_path):
    path = tmp_path / "w.json"
    digest = wr.write_list(wr.listing(archive), path)
    t0 = time.time()
    out = wr.apply(archive, path, digest, now=t0)
    assert out == {"withdrawn": 4, "listed": 4}
    assert _active_hosts(archive) == {"Tiingo", "BALLDONTLIE"}       # the others untouched
    assert archive.withdrawn_count(wr.WHY)["facts"] == 2
    assert archive.withdrawn_count(wr.WHY)["readings"] == 2
    # Recall no longer finds them.
    assert all("Arsenal" not in str(f) and "NVDA" not in str(f)
               for f in ha.recall("line", 30))
    # Reversible: everything back.
    assert archive.restore_withdrawn(wr.WHY) == 4
    assert _active_hosts(archive) == {"API-Sports", "Finnhub", "Tiingo", "BALLDONTLIE"}
    # Withdrawn again; inside the grace period the purge deletes nothing.
    wr.apply(archive, path, digest, now=t0)
    assert wr.purge(archive, now=t0 + 29 * 86400) == 0
    assert archive.withdrawn_count(wr.WHY)["facts"] == 2
    # After it, for good - and only what this withdrawal marked.
    assert wr.purge(archive, now=t0 + 31 * 86400) == 4
    assert archive.withdrawn_count(wr.WHY) == {"facts": 0, "readings": 0, "since": None}
    assert archive.restore_withdrawn(wr.WHY) == 0
    assert _active_hosts(archive) == {"Tiingo", "BALLDONTLIE"}


def test_applying_twice_withdraws_once(archive, tmp_path):
    path = tmp_path / "w.json"
    digest = wr.write_list(wr.listing(archive), path)
    assert wr.apply(archive, path, digest)["withdrawn"] == 4
    assert wr.apply(archive, path, digest)["withdrawn"] == 0


# ------------------------------------------------------------------ the Atlas backend
class _Result:
    def __init__(self, modified=0, deleted=0):
        self.modified_count, self.deleted_count = modified, deleted


def _match(doc, flt):
    for k, want in flt.items():
        have = doc.get(k)
        if isinstance(want, dict):
            for op, v in want.items():
                if op == "$lt" and not (have is not None and have < v):
                    return False
                if op == "$ne" and have == v:
                    return False
        elif have != want:
            return False
    return True


class _Coll:
    def __init__(self):
        self.docs = {}

    def find(self, flt=None, projection=None):
        return [dict(d) for d in self.docs.values() if _match(d, flt or {})]

    def find_one(self, flt):
        found = self.find(flt)
        return found[0] if found else None

    def _apply(self, doc, update, inserting):
        # As MongoDB: `_id` comes from the filter, never the update.
        if "_id" in update.get("$setOnInsert", {}) or "_id" in update.get("$set", {}):
            raise ValueError("Performing an update on the path '_id' would modify the immutable field '_id'")
        for k, v in update.get("$set", {}).items():
            doc[k] = v
        for k in update.get("$unset", {}):
            doc.pop(k, None)
        if inserting:
            for k, v in update.get("$setOnInsert", {}).items():
                doc[k] = v

    def update_one(self, flt, update, upsert=False):
        for d in self.docs.values():
            if _match(d, flt):
                self._apply(d, update, False)
                return _Result(modified=1)
        if upsert:
            doc = {"_id": flt["_id"]}
            self._apply(doc, update, True)
            self.docs[doc["_id"]] = doc
        return _Result()

    def update_many(self, flt, update):
        n = 0
        for d in self.docs.values():
            if _match(d, flt):
                self._apply(d, update, False)
                n += 1
        return _Result(modified=n)

    def delete_one(self, flt):
        for k, d in list(self.docs.items()):
            if _match(d, flt):
                del self.docs[k]
                return _Result(deleted=1)
        return _Result()

    def delete_many(self, flt):
        gone = [k for k, d in self.docs.items() if _match(d, flt)]
        for k in gone:
            del self.docs[k]
        return _Result(deleted=len(gone))

    def count_documents(self, flt):
        return len(self.find(flt))


class _DB(dict):
    def __missing__(self, name):
        self[name] = _Coll()
        return self[name]


def test_the_atlas_backend_withdraws_restores_and_purges():
    db = _DB()
    store = ha.AtlasBackend(database=db)
    for fid, host in (("f1", "API-Sports"), ("f2", "Finnhub"), ("f3", "Tiingo")):
        db["facts"].docs[fid] = {"_id": fid, "kind": "live", "active": True, "host": host,
                                 "title": fid, "published": "2026-10-20",
                                 "superseded_by": ""}
    db["live_series"].docs["r1"] = {"_id": "r1", "source": "Finnhub", "subject": "NVDA",
                                    "as_of": NOW}
    db["live_series"].docs["r2"] = {"_id": "r2", "source": "Tiingo", "subject": "AAPL",
                                    "as_of": NOW}
    rows = store.provider_rows(wr.PROVIDERS)
    assert {r["id"] for r in rows} == {"f1", "f2", "r1"}
    t0 = time.time()
    assert store.withdraw(rows, wr.WHY, t0) == 3
    assert db["facts"].docs["f3"]["active"] is True and "r2" in db["live_series"].docs
    assert "r1" not in db["live_series"].docs and "r1" in db["withdrawn"].docs
    assert store.withdrawn_count(wr.WHY)["facts"] == 2
    assert store.restore_withdrawn(wr.WHY) == 3
    assert db["facts"].docs["f1"]["active"] is True and "r1" in db["live_series"].docs
    store.withdraw(store.provider_rows(wr.PROVIDERS), wr.WHY, t0)
    assert store.purge_withdrawn(wr.WHY, t0 - 1) == 0
    assert store.purge_withdrawn(wr.WHY, t0 + 1) == 3
    assert set(db["facts"].docs) == {"f3"} and not db["withdrawn"].docs
