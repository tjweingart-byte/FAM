"""§433: a listen on the crowd and friends rows is something heard, not a tap.

The server writes a `play` the moment it begins sending audio, so a
two-second tap, a skip and an episode that failed all counted on "What FAM
can't stop playing", "What your friends are listening to" and the friends'
faces on a card. These pin the guardrails: the threshold, the server's clock
against a scrubbed bar, people before listens, and a creation that is not a
listen.
"""
import time

import topics as T


def _store(tmp_path):
    return T.EventStore(str(tmp_path / "e.db"))


def _play(store, user, query, at, topic_id=""):
    store.record(T.Event(user, "play", topic_id, query, (), at))


def _complete(store, user, query, at, topic_id=""):
    store.record(T.Event(user, "complete", topic_id, query, (), at))


def _ranked(store, now):
    tallies = T._tally_listens(
        store.listens_between(now - T.MOST_PLAYED_WINDOW, now + 1), {},
        store.heard_since(now - T.MOST_PLAYED_WINDOW))
    return sorted(tallies.values(), key=T._by_listens)


# --- 1. the threshold, and the server's clock -------------------------------------

def test_the_threshold_is_thirty_seconds_or_thirty_percent():
    assert T.heard_threshold(5) == 30.0
    assert T.heard_threshold(1.5) == 27.0          # 30% of 90s is sooner
    assert T.heard_threshold(5, duration=60) == 18.0  # the real length wins
    assert T.heard_threshold(0) == T.HEARD_SECONDS


def test_a_position_needs_the_time_to_have_passed():
    # Far enough in, and the clock covers it at 2x.
    assert T.counts_as_heard(30, 5, 0, elapsed=15)
    # Not far enough in.
    assert not T.counts_as_heard(29, 5, 0, elapsed=600)
    # The bar dragged to the end a second after the audio began.
    assert not T.counts_as_heard(300, 5, 0, elapsed=1)


def test_progress_is_held_to_the_play_the_server_served(tmp_path):
    store = _store(tmp_path)
    now = time.time()
    # No play served: a progress report alone is never a listen.
    assert not store.note_progress("ana", "why is gold rising", 60, 5, now=now)
    _play(store, "ana", "why is gold rising", now - 5)
    # Scrubbed: 200s claimed 5s after the play began - more than even 2x allows.
    assert not store.note_progress("ana", "why is gold rising", 200, 5, now=now)
    # Heard: 45s in, 40s after it began.
    assert store.note_progress("ana", "Why is gold rising?", 45, 5, now=now + 35)
    assert store.heard_since(now) == {("ana", "why is gold rising")}


# --- 1 and 2. a tap, a skip and a failure are not listens --------------------------

def test_a_tap_and_a_skip_are_not_listens_and_a_finish_is(tmp_path):
    store = _store(tmp_path)
    now = time.time()
    _play(store, "ana", "who won the world series", now - 600)   # tapped, left
    _play(store, "ben", "who won the world series", now - 500)   # skipped at 20s
    store.record(T.Event("ben", "skip", "", "who won the world series", (), now - 480))
    _play(store, "cat", "eagles trade rumours", now - 400)       # finished
    _complete(store, "cat", "eagles trade rumours", now - 310)
    ranked = _ranked(store, now)
    assert [e.query for e in ranked] == ["eagles trade rumours"]


def test_a_complete_scrubbed_to_is_not_a_finish(tmp_path):
    store = _store(tmp_path)
    now = time.time()
    _play(store, "ana", "q one", now - 100)
    _complete(store, "ana", "q one", now - 98)    # two seconds later
    assert _ranked(store, now) == []


def test_a_heard_listen_counts_without_a_finish(tmp_path):
    store = _store(tmp_path)
    now = time.time()
    _play(store, "ana", "q one", now - 100)
    assert store.note_progress("ana", "q one", 45, 5, now=now - 50)
    assert [e.query for e in _ranked(store, now)] == ["q one"]


# --- 3. people before listens -----------------------------------------------------

def test_one_listener_on_repeat_never_outranks_two_people(tmp_path):
    store = _store(tmp_path)
    now = time.time()
    for i in range(6):
        _play(store, "ana", "solo", now - 3000 + i * 100)
        _complete(store, "ana", "solo", now - 3000 + i * 100 + 90)
    for user in ("ben", "cat"):
        _play(store, user, "pair", now - 2000)
        _complete(store, user, "pair", now - 1900)
    ranked = _ranked(store, now)
    assert [e.query for e in ranked] == ["pair", "solo"]
    assert ranked[1].listens == 6     # still the tiebreak


def test_rank_most_played_orders_by_people(tmp_path):
    store = _store(tmp_path)
    now = time.time()
    bank = T.TOPIC_BANK[:2]
    for i in range(4):
        _play(store, "ana", bank[0].query, now - 3000 + i * 200, bank[0].id)
        _complete(store, "ana", bank[0].query, now - 2900 + i * 200, bank[0].id)
    for user in ("ben", "cat"):
        _play(store, user, bank[1].query, now - 2000, bank[1].id)
        _complete(store, user, bank[1].query, now - 1900, bank[1].id)
    got = T.rank_most_played(store, now)
    assert [t.id for t in got[:2]] == [bank[1].id, bank[0].id]


# --- 4. friends: a creation or a tap is not a listen --------------------------------

def test_a_friends_creation_counts_only_once_they_heard_it(tmp_path):
    store = _store(tmp_path)
    now = time.time()
    topic = T.TOPIC_BANK[0]
    authored = [{"author": "ana", "query": topic.query, "created": now - 500}]
    _play(store, "ana", topic.query, now - 500, topic.id)       # asked, left
    assert T.rank_friends(store, {"ana"}, set(), authored=authored, now=now) == []
    assert store.note_progress("ana", topic.query, 40, 5, now=now - 400)
    got = T.rank_friends(store, {"ana"}, set(), authored=authored, now=now)
    assert [t.id for t in got] == [topic.id]


def test_a_face_is_a_friend_who_heard_it(tmp_path):
    store = _store(tmp_path)
    now = time.time()
    tiles = [{"id": "t", "query": "q"}]
    _play(store, "ana", "q", now - 300, "t")                 # a tap
    _play(store, "ben", "q", now - 200, "t")
    _complete(store, "ben", "q", now - 100, "t")             # heard to the end
    assert T.friends_who_listened(store, ["ana", "ben"], tiles, now=now) == {"t": ["ben"]}


# --- the store keeps nothing a deletion or a wipe leaves behind ----------------------

def test_forget_and_clear_take_the_heard_listens(tmp_path):
    store = _store(tmp_path)
    store.mark_heard("ana", "q", time.time())
    store.mark_heard("ben", "q", time.time())
    store.forget("ana")
    assert store.heard_since(0) == {("ben", "q")}
    store.clear()
    assert store.heard_since(0) == set()


def test_taste_never_reads_a_heard_listen(tmp_path):
    store = _store(tmp_path)
    store.mark_heard("ana", "q", time.time())
    assert store.for_user("ana") == []


# --- 6. the admin's report -----------------------------------------------------------

def test_the_report_says_tapped_against_heard_and_names_nobody(tmp_path):
    store = _store(tmp_path)
    now = time.time()
    _play(store, "ana", "q", now - 300)
    _play(store, "ben", "q", now - 300)
    _complete(store, "ben", "q", now - 200)
    report = T.listen_report(store, now)
    assert report["tapped"] == 2 and report["heard"] == 1
    assert report["questions"] == [{"question": "q", "tapped": 2, "heard": 1,
                                    "finished": 1}]
    assert "ana" not in repr(report) and "ben" not in repr(report)


def test_the_admin_page_draws_the_report():
    import pathlib
    page = (pathlib.Path(__file__).resolve().parent.parent
            / "admin_ui" / "tracker.html").read_text(encoding="utf-8")
    assert 'id="listens"' in page and "drawListens(s.listens)" in page
