"""A worker saying where it is, and the app deciding whether to believe it.

The registry is what survives a pod being replaced: the new one introduces
itself and is in service within a heartbeat. What these pin is the boundary
around that - who may say it, what an address is allowed to look like, and how
long a claim lasts once the thing making it has stopped.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import voice_registry  # noqa: E402


@pytest.fixture
def store(tmp_path):
    return voice_registry.VoiceRegistry(str(tmp_path / "voice.db"))


POD = "https://abc123-8001.proxy.runpod.net"


# --- what an address may be ------------------------------------------------

def test_a_worker_is_recorded_with_what_it_said_about_itself(store):
    row = store.register({"url": POD, "mode": "http", "port": 8001,
                          "contract": 1, "sample_rate": 24000,
                          "commit": "abc1234", "image": "fam-voice:3"})
    assert row.url == POD and row.contract == 1 and row.commit == "abc1234"
    assert row.ready is True


def test_the_route_is_trimmed_rather_than_refused(store):
    """An operator registering the URL they were testing with has said
    something unambiguous; a second /synth appended to it is a 404."""
    assert store.register({"url": POD + "/synth"}).url == POD


def test_plain_http_is_refused_on_the_open_internet(store):
    """The bearer token and every sentence of the script ride on it."""
    with pytest.raises(voice_registry.RegistryError) as caught:
        store.register({"url": "http://abc123-8001.proxy.runpod.net"})
    assert "https" in str(caught.value)


def test_a_worker_on_this_machine_may_be_plain(store):
    assert store.register({"url": "http://localhost:8001"}).url \
        == "http://localhost:8001"


def test_an_address_with_a_path_is_refused(store):
    with pytest.raises(voice_registry.RegistryError):
        store.register({"url": "https://example.com/somebody/else"})


def test_nonsense_is_refused_with_a_sentence(store):
    for bad in ("", "not a url", "ftp://pod/", None):
        with pytest.raises(voice_registry.RegistryError):
            store.register({"url": bad})


# --- a registration is a claim with a clock --------------------------------

def test_a_heartbeat_updates_rather_than_duplicating(store):
    first = store.register({"url": POD}, at=1000.0)
    again = store.register({"url": POD, "detail": "still here"}, at=1060.0)
    assert len(store.all()) == 1
    assert again.first_seen == first.first_seen, "uptime was lost"
    assert again.last_seen == 1060.0


def test_a_worker_that_stopped_heartbeating_stops_being_offered(store):
    store.register({"url": POD}, at=1000.0)
    assert [r.url for r in store.live(ttl=300.0, at=1200.0)] == [POD]
    assert store.live(ttl=300.0, at=2000.0) == []


def test_it_is_still_readable_after_it_expires(store):
    """The doctor has to be able to say "it registered and then stopped",
    which is a different sentence from "nothing ever registered"."""
    store.register({"url": POD}, at=1000.0)
    assert [r.url for r in store.all()] == [POD]


def test_a_worker_on_its_way_out_can_say_so(store):
    store.register({"url": POD})
    store.forget(POD)
    assert store.all() == []


def test_forgetting_something_unregisterable_does_not_raise(store):
    store.forget("not a url")


def test_the_newest_heartbeat_is_offered_first(store):
    store.register({"url": POD}, at=1000.0)
    store.register({"url": "https://def456-8001.proxy.runpod.net"}, at=1100.0)
    assert store.live(at=1200.0)[0].url.startswith("https://def456")


def test_the_table_does_not_grow_without_bound(store):
    for index in range(voice_registry.MAX_ROWS + 5):
        store.register({"url": f"https://pod-{index}.example"}, at=1000.0 + index)
    assert len(store.all()) == voice_registry.MAX_ROWS


def test_what_a_worker_says_about_itself_reaches_the_report(store):
    store.register({"url": POD, "mode": "http", "sample_rate": 24000,
                    "image": "fam-voice:3"}, at=1000.0)
    shown = store.live(at=1030.0)[0].as_dict()
    assert shown["image"] == "fam-voice:3" and shown["age_seconds"] >= 0
    assert "token" not in shown


# --- the one address a pod has that no proxy fronts (§117) ----------------

def test_a_direct_pod_address_is_accepted_when_it_was_allowed(store, monkeypatch):
    """RunPod's proxy is the only TLS address a pod has, and that proxy is
    Cloudflare, which serves a browser and refuses a server. So on a pod the
    choice is a raw TCP port or no voice at all - and it is a decision
    somebody makes rather than a default they discover."""
    import voice_control

    monkeypatch.setattr(voice_control, "allow_plain_http", lambda: True)
    assert store.register({"url": "http://203.0.113.7:40411"}).url \
        == "http://203.0.113.7:40411"


def test_the_refusal_names_the_switch_that_permits_it(store):
    with pytest.raises(voice_registry.RegistryError) as caught:
        store.register({"url": "http://203.0.113.7:40411"})
    assert "VOICE_ALLOW_PLAIN_HTTP" in str(caught.value)


def test_the_registry_and_the_ladder_cannot_disagree(monkeypatch):
    """An address one accepts and the other refuses is a worker that
    registers successfully and is never used."""
    import voice_control

    monkeypatch.setattr(voice_control, "allow_plain_http", lambda: False)
    assert voice_registry._plain_http_allowed() is False
    monkeypatch.setattr(voice_control, "allow_plain_http", lambda: True)
    assert voice_registry._plain_http_allowed() is True
