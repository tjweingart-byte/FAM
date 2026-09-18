"""Save for later: a pointer to an episode, and the toggle that makes one."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import saved as saved_mod


@pytest.fixture
def store(tmp_path):
    return saved_mod.SavedStore(str(tmp_path / "saved.db"))


# --- saving ---------------------------------------------------------------

def test_saving_the_same_episode_twice_is_one_item(store):
    """Keyed on (question, length), which is also the script cache's key: two
    saves differing only in something the cache ignores are one episode."""
    first = store.save("u", "why bonds move", 3, title="Bonds")
    second = store.save("u", "why bonds move", 3, title="Bonds")
    assert first.id == second.id
    assert len(store.items("u")) == 1


def test_saving_again_into_a_folder_files_it_rather_than_failing(store):
    """From the listener's side they pressed save and it is saved, which is
    true either way - so the second press should do the useful thing."""
    folder = store.create_folder("u", "Commute")
    store.save("u", "why bonds move", 3)
    again = store.save("u", "why bonds move", 3, folder_id=folder["id"])
    assert again.folder_id == folder["id"]


def test_an_episode_with_no_question_cannot_be_saved(store):
    with pytest.raises(saved_mod.SavedError):
        store.save("u", "   ", 3)


def test_saving_into_somebody_elses_folder_is_refused(store):
    theirs = store.create_folder("them", "Private")
    with pytest.raises(saved_mod.SavedError):
        store.save("u", "why bonds move", 3, folder_id=theirs["id"])


def test_one_listeners_shelf_is_not_anothers(store):
    store.save("u", "why bonds move", 3)
    assert store.items("them") == []


# --- folders --------------------------------------------------------------

def test_folders_count_what_is_in_them(store):
    folder = store.create_folder("u", "Commute")
    store.save("u", "a question", 3, folder_id=folder["id"])
    assert store.folders("u")[0]["items"] == 1


def test_two_folders_cannot_share_a_name(store):
    store.create_folder("u", "Commute")
    with pytest.raises(saved_mod.SavedError):
        store.create_folder("u", "commute")


def test_deleting_a_folder_unfiles_its_episodes_rather_than_deleting_them(store):
    """Deleting somebody's saved episodes because they tidied their folders is
    the kind of surprise that stops people using a feature - and a download
    inside it would be bytes on their phone held against their limit with
    nothing pointing at them."""
    folder = store.create_folder("u", "Commute")
    store.save("u", "a question", 3, folder_id=folder["id"])
    assert store.delete_folder("u", folder["id"]) == 1
    remaining = store.items("u")
    assert len(remaining) == 1
    assert remaining[0].folder_id == ""


def test_a_folder_needs_a_name(store):
    with pytest.raises(saved_mod.SavedError):
        store.create_folder("u", "   ")


# --- the toggle -----------------------------------------------------------
#
# Save is a toggle now, pressed in the player, so the pair it acts on is the
# question and the length - the script cache's key - and never a row id the
# player does not have.

def test_unsaving_by_question_and_length_takes_it_off_the_shelf(store):
    store.save("u", "why bonds move", 3)
    assert store.unsave("u", "why bonds move", 3) is True
    assert store.find("u", "why bonds move", 3) is None


def test_unsaving_matches_the_length_as_well_as_the_question(store):
    """Two lengths of one question are two episodes - that is what the cache
    key says - so un-pressing save on one must not clear the other."""
    store.save("u", "why bonds move", 3)
    store.save("u", "why bonds move", 10)
    store.unsave("u", "why bonds move", 3)
    assert store.find("u", "why bonds move", 3) is None
    assert store.find("u", "why bonds move", 10) is not None


def test_unsaving_something_that_is_not_saved_is_not_an_error(store):
    """A toggle pressed twice quickly, or on two devices. Reporting False is
    enough; raising would make the second tap look like a failure."""
    assert store.unsave("u", "never saved", 3) is False


def test_unsaving_is_scoped_to_the_listener(store):
    store.save("them", "their question", 3)
    assert store.unsave("u", "their question", 3) is False
    assert store.find("them", "their question", 3) is not None


def test_the_shelf_carries_no_download_state_any_more(store):
    """The feature is removed, not switched off. A `downloaded` key coming
    back would be a client's invitation to draw a control for it."""
    item = store.save("u", "why bonds move", 3).as_dict()
    for gone in ("downloaded", "bytes", "downloaded_at", "estimated_bytes"):
        assert gone not in item, f"{gone} is still on a saved item"
    for gone in ("download_status", "reserve_download", "confirm_download",
                 "release_download", "estimated_bytes"):
        assert not hasattr(saved_mod, gone) and not hasattr(store, gone), \
            f"saved.py still offers {gone}"


# --- deletion -------------------------------------------------------------

def test_forget_erases_the_whole_shelf(store):
    folder = store.create_folder("u", "Commute")
    store.save("u", "a question", 3, folder_id=folder["id"])
    store.save("them", "their question", 3)
    store.forget("u")
    assert store.items("u") == [] and store.folders("u") == []
    assert len(store.items("them")) == 1
