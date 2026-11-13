"""§585: the category tree goes out to another reader as a CSV and comes back
as proposed edits; an accepted edit is the owner's, and nothing that grows
the tree (the sweep, the writer, the seed, the placer) undoes it."""
from __future__ import annotations

import csv
import io

import pytest
from fastapi.testclient import TestClient

import categories as C
import category_review as R
import topics

ADMIN = {"X-Admin-Token": "sekrit"}


@pytest.fixture
def store(tmp_path):
    s = C.CategoryStore(str(tmp_path / "categories.db"))
    C.apply_seed(s)
    s.mint("penguins", parent_id="nfl", source=C.SOURCE_SEARCH, listeners=4)
    return s


def test_the_download_lists_every_node_with_its_path(store):
    rows = list(csv.DictReader(io.StringIO(R.export_csv(store, {"bengals": 3}))))
    assert tuple(rows[0]) == R.EXPORT_COLUMNS
    assert {r["node"] for r in rows} == set(store.nodes())
    bengals = next(r for r in rows if r["node"] == "bengals")
    assert bengals["path"] == "sports > american football > nfl > bengals"
    assert bengals["episodes"] == "3"
    nfl = next(r for r in rows if r["node"] == "nfl")
    assert int(nfl["episodes_below"]) >= 3


def test_an_upload_changes_nothing_and_is_read_from_a_chat_reply(store):
    before = {k: n.parent_id for k, n in store.nodes().items()}
    reply = ("Here are the fixes:\n```csv\naction,node,target,confidence,reason\n"
             "move,penguins,nhl,high,Penguins are an NHL team\n"
             "explode,bengals,,high,nonsense\n"
             "move,unicorns,nhl,high,not in the tree\n"
             "add,metropolitan division,nhl,medium,a missing level\n"
             "move,penguins,metropolitan division,medium,its division\n"
             "move,nfl,bengals,low,a loop\n"
             "```\n")
    out = R.read_upload(reply, store)
    assert {k: n.parent_id for k, n in store.nodes().items()} == before
    got = [(p["action"], p["node"], p["target"]) for p in out["proposals"]]
    assert got == [("move", "penguins", "nhl"),
                   ("add", "metropolitan division", "nhl"),
                   ("move", "penguins", "metropolitan division")]
    assert out["proposals"][2]["needs"] == "metropolitan division"
    why = {r["node"]: r["why"] for r in out["refused"]}
    assert set(why) == {"bengals", "unicorns", "nfl"}
    assert "loop" in why["nfl"]


def test_a_file_without_the_columns_is_refused_in_words(store):
    with pytest.raises(ValueError, match="action and node"):
        R.read_upload("name,say\nx,y\n", store)


def test_an_owner_move_is_never_undone_by_the_placer(store):
    R.apply(store, "move", "penguins", "nhl")
    assert store.get("penguins").parent_id == "nhl"
    assert store.get("penguins").depth == store.get("nhl").depth + 1
    C.apply_paths(store, [{"subject": "penguins",
                           "path": ["sports", "american football", "nfl", "penguins"]}])
    assert store.get("penguins").parent_id == "nhl"


def test_a_removed_node_is_never_grown_back(store, tmp_path):
    children = [k for k, n in store.nodes().items() if n.parent_id == "nfl"]
    R.apply(store, "remove", "nfl")
    assert store.get("nfl") is None
    for child in children:
        assert store.get(child).parent_id == "american football"
    # Not by a sweep, a writer, or the seed at the next boot.
    assert store.mint("nfl", parent_id="american football", source=C.SOURCE_WRITER) is None
    C.apply_seed(store)
    assert store.get("nfl") is None
    # A fresh process on the same database keeps the decision.
    again = C.CategoryStore(store.path)
    C.apply_seed(again)
    assert again.get("nfl") is None


def test_a_merged_phrase_reads_as_the_node_it_went_into(store):
    R.apply(store, "merge", "penguins", "nhl")
    assert store.get("penguins") is None
    assert "nhl" in store.match("the penguins won last night")


def test_rename_and_add_survive_a_wipe(store):
    R.apply(store, "add", "metropolitan division", "nhl")
    R.apply(store, "rename", "penguins", "pens hockey club")
    R.apply(store, "move", "pens hockey club", "metropolitan division")
    assert store.get("pens hockey club").source == C.SOURCE_OWNER
    store.clear()
    C.apply_seed(store)
    assert store.get("metropolitan division").parent_id == "nhl"
    assert store.get("pens hockey club").parent_id == "metropolitan division"
    assert store.get("penguins") is None


def test_owner_nodes_are_never_pruned(store):
    R.apply(store, "add", "metropolitan division", "nhl")
    store.prune(now=10**12)
    assert store.get("metropolitan division") is not None


def test_forgetting_an_edit_lets_the_phrase_grow_again(store):
    R.apply(store, "remove", "penguins")
    assert store.owner_undo("penguins")
    assert store.mint("penguins", parent_id="nhl", source=C.SOURCE_SEARCH) is not None


def test_an_edit_that_cannot_apply_says_why(store):
    with pytest.raises(ValueError, match="not in the tree"):
        R.apply(store, "move", "unicorns", "nhl")
    with pytest.raises(ValueError, match="loop"):
        R.apply(store, "move", "american football", "bengals")
    with pytest.raises(ValueError, match="cannot be a category"):
        R.apply(store, "add", "week 5", "nfl")


@pytest.fixture
def client(monkeypatch, store):
    import app as appmod

    monkeypatch.setattr(appmod, "ADMIN_TOKEN", "sekrit")
    monkeypatch.setattr(topics, "category_tree", lambda: store)
    return TestClient(appmod.app)


def test_the_round_trip_is_for_admins_only(client):
    assert client.get("/api/admin/categories/export").status_code in (401, 403, 404)
    assert client.post("/api/admin/categories/edit",
                       json={"action": "remove", "node": "nfl"}).status_code in (401, 403, 404)


def test_the_round_trip_over_http(client, store):
    got = client.get("/api/admin/categories/export", headers=ADMIN)
    assert got.status_code == 200 and "fam-categories-" in got.headers["content-disposition"]
    assert "penguins" in got.text
    assert "action,node,target" in client.get(
        "/api/admin/categories/review-prompt", headers=ADMIN).json()["prompt"]
    read = client.post("/api/admin/categories/import", headers=ADMIN,
                       json={"csv": "action,node,target,confidence,reason\n"
                                    "move,penguins,nhl,high,NHL\n"}).json()
    assert read["proposals"][0]["node"] == "penguins"
    assert store.get("penguins").parent_id == "nfl"
    done = client.post("/api/admin/categories/edit", headers=ADMIN,
                       json={"action": "move", "node": "penguins", "target": "nhl"})
    assert done.status_code == 200 and store.get("penguins").parent_id == "nhl"
    bad = client.post("/api/admin/categories/edit", headers=ADMIN,
                      json={"action": "move", "node": "unicorns", "target": "nhl"})
    assert bad.status_code == 422
    edits = client.get("/api/admin/categories/edits", headers=ADMIN).json()["edits"]
    assert edits == [{"node": "penguins", "action": "placed", "target": "nhl"}]
    assert client.delete("/api/admin/categories/edits/penguins", headers=ADMIN).json()["ok"]
