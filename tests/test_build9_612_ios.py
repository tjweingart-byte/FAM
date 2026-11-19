"""Build 9: the iPhone catches up with §612 (PROBLEMS.md §617).

§612 made every saved, vibed and shared episode replay by its kept id, so
a friend's link, a save or a vibe never writes the episode again. The web
sent and read the id; the iPhone did neither, so from the app the owner's
"it generated a full new one" could still happen. These read the Swift's
own source (nothing here compiles it; `ios.yml` does).
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
IOS = ROOT / "ios" / "FAM" / "FAM"


def swift(rel: str) -> str:
    return (IOS / rel).read_text()


def func(source: str, head: str) -> str:
    start = source.index(head)
    return source[start:source.index("\n    }\n", start)]


def test_every_write_sends_the_episode():
    api = swift("API.swift")
    for head in ("func save(_ ep: EpisodeKey", "func vibe(_ ep: EpisodeKey",
                 "func share(_ ep: EpisodeKey", "func saveShared(query:"):
        assert '"episode": Self.cut(episode, 80)' in func(api, head), head
    editor = swift("Views/StoryEditor.swift")
    assert 'body["episode"] = FAMAPI.cut(episode, 80)' in func(editor, "func vibeStory(")


def test_every_read_decodes_the_episode():
    api = swift("API.swift")
    for name in ("struct SavedItem: Decodable", "struct Story: Decodable",
                 "struct VibeRow: Decodable", "struct VibePost: Decodable"):
        body = api[api.index(name):]
        body = body[:body.index("\n}\n")]
        assert "let episode: String?" in body, name


def test_the_player_sends_the_episode_it_served():
    now = swift("NowPlaying.swift")
    assert "private func keptID(_ ep: Episode) -> String" in now
    assert "player.episodeID" in func(now, "private func keptID(")
    assert "try await api.save(ep.key, title: heard, episode: kept)" in now
    assert "episode: keptID(ep))" in now                       # VIBE!
    sheets = swift("Views/PlayerSheets.swift")
    assert "episode: player.episodeID)" in sheets               # the story editor
    assert "model.api.share(ep.key, title: ep.title, episode: ep.episode)" in sheets


def test_saved_vibes_stories_and_posts_play_by_id():
    you = swift("YourFAM.swift")
    assert 'ep.episodeID = item.episode ?? ""' in func(you, "func playSaved(")
    assert "ep.episodeID = episode" in func(you, "func playOwnVibe(")
    assert 'episode: v.episode ?? ""' in swift("Views/Shelves.swift")
    assert 'ep.episodeID = story.episode ?? ""' in swift("Views/Stories.swift")
    assert "ep.episodeID = p.episode" in swift("Views/VibePosts.swift")


def test_saves_from_a_story_a_post_and_a_message_carry_it():
    assert 'episode: story.episode ?? "")' in swift("Views/Stories.swift")
    assert 'source: "vibe", episode: episode)' in swift("Views/VibePosts.swift")
    assert 'episode: m.episode ?? "")' in swift("Views/ChatView.swift")


def test_a_gone_episode_still_asks_the_question():
    """Replaying by id is safe because a 409 falls back to the question
    (§288's path), so an id never leaves a listener with nothing."""
    model = swift("AppModel.swift")
    assert re.search(r"status == 409 && !\(pending\?\.episodeID\.isEmpty", model)
    assert 'again.episodeID = ""' in model


def test_build_9_or_later():
    # Build 10 (§675) carries §612 forward; its own test pins the number.
    project = (ROOT / "ios" / "FAM" / "project.yml").read_text()
    build = re.search(r'CURRENT_PROJECT_VERSION: "(\d+)"', project)
    assert build and int(build.group(1)) >= 9
