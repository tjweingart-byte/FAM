"""Every database is found from the project root, never the working directory.

The bug this pins was silent and pointed the wrong way: a bare filename follows
the cwd, so starting the server from somewhere else created a second, empty set
of files without raising anything. The app came up with a cold cache, an empty
feed and no echoes, and looked like a broken feature rather than a wrong path.

The first test is the whole point - it changes directory and asserts the stores
do not follow.
"""
from __future__ import annotations

import importlib
import os
import re
import pathlib
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import accounts as ACC
import metering as ME  # noqa: E402
import attachments as A  # noqa: E402
import messages as MSG  # noqa: E402
import mixes as M  # noqa: E402
import paths  # noqa: E402
import preferences as P  # noqa: E402
import categories as CAT  # noqa: E402
import quotas as Q  # noqa: E402
import saved as SV  # noqa: E402
import sharing as SH  # noqa: E402
import social as S  # noqa: E402
import topics as T  # noqa: E402
import voice_bank as VB  # noqa: E402
import voice_registry as VR  # noqa: E402
import trending_bank as TB  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: Every store, as (env var, filename, constructor). The constructors are named
#: here because a class cannot be discovered from a string, but **the list of
#: paths is not**: see `DECLARED_VARS` below.
STORES = [
    ("MYFAM_DB", "myfam.db", T.EventStore),
    ("SOCIAL_DB", "social.db", S.SocialStore),
    ("MIXES_DB", "mixes.db", M.MixStore),
    ("ATTACHMENTS_PATH", "attachments.db", A.AttachmentStore),
    ("ACCOUNTS_DB", "accounts.db", ACC.AccountStore),
    ("PREFS_DB", "preferences.db", P.PreferenceStore),
    ("METERING_DB", "metering.db", ME.MeterStore),
    ("MESSAGES_DB", "messages.db", MSG.MessageStore),
    ("SAVED_DB", "saved.db", SV.SavedStore),
    ("SHARES_DB", "shares.db", SH.ShareStore),
    ("QUOTAS_DB", "quotas.db", Q.QuotaStore),
    ("VOICE_REGISTRY_DB", "voice_registry.db", VR.VoiceRegistry),
    ("VOICE_BANK_DB", "voice_bank.db", VB.VoiceBank),
    ("CATEGORIES_DB", "categories.db", CAT.CategoryStore),
    ("TRENDING_BANK_DB", "trending_bank.db", TB.BankStore),
]

#: Every `data_path(...)` call in the app, read out of the source.
#:
#: **This is the guard, and the hand-written list above is not.** The
#: deployment check used to compare the Dockerfile against `STORES`, which is
#: also maintained by hand - so four stores added after both were written
#: (messages, saved, shares, quotas) were missing from each, and the two
#: agreed with each other about a set that was wrong. A deployment therefore
#: discarded every conversation, saved episode and share link on each redeploy
#: while the accounts beside them survived, and nothing failed.
#:
#: Derived, this cannot happen: a store is discovered the moment it calls
#: `data_path`, whether or not anybody remembered this file.
_CALL = re.compile(r'data_path\(\s*"([A-Z_]+)"\s*,\s*"([^"]+)"')


def _declared() -> dict:
    found = {}
    for module in sorted(ROOT.glob("*.py")):
        for var, filename in _CALL.findall(module.read_text()):
            found[var] = filename
    return found


DECLARED = _declared()
ALL_VARS = sorted(DECLARED)

#: Stores that are legitimately absent from a health report.
#:
#: `VOICE_REGISTRY_DB` is opened only where workers register themselves, and
#: `_database_report` says why it is reported conditionally: a store listed as
#: missing on every machine that never switched the feature on is a health
#: page teaching people to ignore it.
#:
#: Anything added here needs that kind of reason written beside it. The default
#: is that a store the app opens is a store the health page names.
#: `TRENDING_BANK_DB` is the same case (§139): the first edition build
#: creates it, and a health page that opened it would create a database on
#: every machine that never built one - which is how a stray copy once
#: appeared in the project root. Reported once it exists.
#: `VOICE_BANK_DB` (§147) is opened by the first request that asks which
#: voice to use, and reported from then on - the registry's rule again.
LAZY_STORES = {"VOICE_REGISTRY_DB", "TRENDING_BANK_DB", "VOICE_BANK_DB"}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ALL_VARS:
        monkeypatch.delenv(var, raising=False)


# --- the bug itself -------------------------------------------------------


def test_the_working_directory_does_not_move_the_databases(monkeypatch, tmp_path):
    """Start the app from anywhere; it opens the same files."""
    here = {var: paths.data_path(var, name) for var, name, _c in STORES}
    monkeypatch.chdir(tmp_path)
    assert {var: paths.data_path(var, name) for var, name, _c in STORES} == here


def test_a_store_built_from_elsewhere_writes_to_the_project_root(monkeypatch, tmp_path):
    """The seed_demo case: seed from one directory, serve from another. It
    wrote to the cwd, so Explore stayed empty however much you tapped it."""
    monkeypatch.chdir(tmp_path)
    store = T.EventStore()
    assert pathlib.Path(store.path).parent == ROOT
    # Named files rather than *.db: a fixture may legitimately put its own
    # database in tmp_path, and the claim here is only that *these* did not
    # follow the working directory.
    strays = [f for _v, f, _c in STORES if (tmp_path / f).exists()]
    assert not strays, f"a store followed the cwd: {strays}"


# --- resolution rules -----------------------------------------------------


@pytest.mark.parametrize("var, filename, ctor", STORES)
def test_every_store_defaults_to_an_absolute_path(var, filename, ctor, tmp_path):
    resolved = pathlib.Path(paths.data_path(var, filename))
    assert resolved.is_absolute()
    assert resolved == ROOT / filename


@pytest.mark.parametrize("var, filename, ctor", STORES)
def test_an_absolute_env_var_is_used_exactly(var, filename, ctor, monkeypatch, tmp_path):
    wanted = tmp_path / "elsewhere" / filename
    wanted.parent.mkdir()
    monkeypatch.setenv(var, str(wanted))
    assert paths.data_path(var, filename) == str(wanted)
    assert ctor().path == str(wanted)


@pytest.mark.parametrize("var, filename, ctor", STORES)
def test_an_explicit_path_still_wins(var, filename, ctor, tmp_path):
    """Tests pass paths directly; that must keep working."""
    given = str(tmp_path / filename)
    assert ctor(given).path == given


def test_a_relative_env_var_resolves_to_the_project_root_and_says_so(
    monkeypatch, tmp_path, caplog
):
    """Never the cwd, and never silently - the ambiguity is the bug."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MYFAM_DB", "somewhere.db")
    with caplog.at_level("WARNING"):
        resolved = paths.data_path("MYFAM_DB", "myfam.db")
    assert resolved == str(ROOT / "somewhere.db")
    assert "relative" in caplog.text and "MYFAM_DB" in caplog.text


def test_a_tilde_path_is_expanded(monkeypatch):
    monkeypatch.setenv("SOCIAL_DB", "~/fam-social.db")
    resolved = pathlib.Path(paths.data_path("SOCIAL_DB", "social.db"))
    assert resolved.is_absolute() and "~" not in str(resolved)
    assert resolved == pathlib.Path.home() / "fam-social.db"


def test_an_empty_env_var_is_treated_as_unset(monkeypatch):
    """An exported-but-blank variable is a deployment slip, not a request to
    open a file called ''."""
    monkeypatch.setenv("MIXES_DB", "   ")
    assert paths.data_path("MIXES_DB", "mixes.db") == str(ROOT / "mixes.db")


def test_the_cache_path_follows_the_same_rule(monkeypatch, tmp_path):
    import config

    monkeypatch.setenv("FAM_IGNORE_DOTENV", "1")
    monkeypatch.setenv("CACHE_PATH", str(tmp_path / "c.db"))
    importlib.reload(config)
    try:
        assert config.settings.cache_path == str(tmp_path / "c.db")
        monkeypatch.delenv("CACHE_PATH")
        importlib.reload(config)
        assert config.settings.cache_path == str(ROOT / "scripts.db")
    finally:
        importlib.reload(config)


# --- the deployment has to name all of them -------------------------------


def test_the_dockerfile_puts_every_database_on_the_mounted_disk():
    """A store added later must be added to the Dockerfile too, and `ALL_VARS`
    is now read out of the source so that this cannot be satisfied by two
    hand-written lists agreeing with each other about the wrong set - which is
    how messages, saved, shares and quotas were discarded on every redeploy."""
    dockerfile = (ROOT / "Dockerfile").read_text()
    missing = [v for v in ALL_VARS if f"{v}=/data/" not in dockerfile]
    assert not missing, f"not pinned to the mounted disk in the Dockerfile: {missing}"


def test_the_hand_written_list_covers_every_declared_store():
    """`STORES` names constructors, which cannot be discovered from a string,
    so it stays by hand - but it must not fall behind the derived set, or the
    per-store resolution tests above quietly stop covering a new store."""
    # The script cache is the one exemption, by name rather than by silence:
    # it has two backends (memory and sqlite) so there is no single
    # constructor to parametrise, and `test_the_cache_path_follows_the_same_rule`
    # covers it on its own terms.
    missing = sorted(set(DECLARED) - {v for v, _f, _c in STORES} - {"CACHE_PATH"})
    assert not missing, f"declared by a module but not tested here: {missing}"


def test_every_declared_store_is_discovered_by_filename_too():
    """The derived list has to carry the filename as well as the variable: the
    Dockerfile check only needs the name, and the resolution checks need both."""
    for var, filename in DECLARED.items():
        assert filename.endswith(".db"), f"{var} -> {filename}"


def test_the_example_ships_no_literal_database_path():
    """A default that named a directory would be wrong on every machine but
    the one it was written on, and test_env_example compares live values."""
    active = [
        line.split("=")[0].strip()
        for line in (ROOT / ".env.example").read_text().splitlines()
        if line.strip() and not line.strip().startswith("#") and "=" in line
    ]
    assert not [v for v in ALL_VARS if v in active], (
        "a database path is set in .env.example; it should be commented out, "
        "because the working default is derived rather than written down"
    )


def test_the_example_documents_every_one_of_them():
    example = (ROOT / ".env.example").read_text()
    assert not [v for v in ALL_VARS if v not in example], "an undocumented path"


# --- the static files had the same bug, and hid it -------------------------


def test_the_interface_is_served_from_the_project_root_not_the_cwd(monkeypatch, tmp_path):
    """`StaticFiles(directory="static")` was cwd-relative too. It failed loudly
    - the app would not start from another directory at all - which is exactly
    why nobody reached the quiet database version of the same bug behind it."""
    import app as appmod

    monkeypatch.chdir(tmp_path)
    mount = next(r for r in appmod.app.routes if getattr(r, "name", "") == "static")
    served = pathlib.Path(mount.app.directory)
    assert served.is_absolute()
    assert served == ROOT / "static"


def test_the_app_module_names_no_bare_relative_directory():
    """The guard against the next one. Any `directory="..."` in app.py that is
    not built from PROJECT_ROOT will follow whoever started the process."""
    source = (ROOT / "app.py").read_text()
    bare = [
        line.strip()
        for line in source.splitlines()
        if 'directory="' in line and "PROJECT_ROOT" not in line
    ]
    assert not bare, f"cwd-relative directory in app.py: {bare}"


# --- a missing directory must name the setting, not sqlite's complaint ------


def test_a_missing_directory_is_created(monkeypatch, tmp_path):
    """`.env.example` invites people to point these somewhere; the directory
    not existing yet is the ordinary first case, not an error."""
    wanted = tmp_path / "new" / "deeper" / "myfam.db"
    monkeypatch.setenv("MYFAM_DB", str(wanted))
    assert paths.data_path("MYFAM_DB", "myfam.db") == str(wanted)
    assert wanted.parent.is_dir()
    assert T.EventStore().path == str(wanted)


def test_a_directory_that_cannot_be_made_names_the_variable(monkeypatch, tmp_path):
    """The old failure was `unable to open database file` from inside sqlite3
    at import - true, and naming neither the setting nor the directory."""
    blocker = tmp_path / "a-file-not-a-directory"
    blocker.write_text("")
    monkeypatch.setenv("SOCIAL_DB", str(blocker / "social.db"))
    with pytest.raises(paths.DataPathError) as excinfo:
        paths.data_path("SOCIAL_DB", "social.db")
    message = str(excinfo.value)
    assert "SOCIAL_DB" in message, "the error must name the setting to change"
    assert str(blocker) in message, "and the directory that could not be made"


# --- health has to report storage, not just the cache ----------------------


def test_health_reports_every_database_with_a_real_read(monkeypatch, tmp_path):
    """§52: readiness is performed, not confirmed. `status: ok` used to be
    returned while four of the five could be pointed anywhere."""
    from fastapi.testclient import TestClient

    import app as appmod

    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    body = TestClient(appmod.app).get("/api/health").json()

    # **Derived, not a second hand-written list.** This used to compare the
    # reported names against a set typed out here, which is the mistake §107
    # names in this very file: two lists somebody types agreeing with each
    # other is one mistake made twice and then compared to itself. It passed
    # happily while `categories` was missing from the health report, which is
    # exactly what it exists to catch.
    #
    # So the assertion is now against `DECLARED` - every `data_path(...)` call
    # read out of the modules. A store added to the app and not to
    # `_database_report` fails here rather than being discovered by somebody
    # running `storage_doctor` and counting.
    reported = {entry["env_var"] for entry in body["databases"]}
    missing = sorted(set(DECLARED) - reported - LAZY_STORES)
    assert not missing, (
        f"{missing} are opened by the app and not reported by /api/health, so "
        "nothing would say whether a redeploy erases them"
    )
    for entry in body["databases"]:
        assert entry["readable"] is True, f"{entry['name']} did not open: {entry}"
        assert entry["writable"] is True
        assert pathlib.Path(entry["path"]).is_absolute()
        assert entry["env_var"] in ALL_VARS


def test_health_says_a_broken_database_is_broken(monkeypatch, tmp_path):
    """The failure that matters: a path that does not open must not be
    reported as ok."""
    from fastapi.testclient import TestClient

    import app as appmod

    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    not_a_database = tmp_path / "rubbish.db"
    not_a_database.write_bytes(b"this is not a sqlite file, not even close")
    monkeypatch.setattr(appmod.EVENTS, "path", str(not_a_database))

    body = TestClient(appmod.app).get("/api/health").json()
    events = next(e for e in body["databases"] if e["name"] == "events")
    assert events["readable"] is False
    assert "error" in events, "a broken database must say what went wrong"


# --- and whether a redeploy would erase them -------------------------------
#
# §107. The Dockerfile was read by hand to answer this and was wrong twice, and
# from outside a wiped database and a new install look identical. So the
# running server measures it: a mounted volume is a different filesystem, and
# `st_dev` is a fact about the machine rather than an echo of a setting.


def test_every_database_says_whether_it_survives_a_redeploy(monkeypatch):
    from fastapi.testclient import TestClient

    import app as appmod

    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    body = TestClient(appmod.app).get("/api/health").json()
    for entry in body["databases"]:
        assert entry["persistence"] in {"disk", "image", "memory", "unknown"}, entry


def test_a_database_on_the_code_s_own_filesystem_goes_with_the_image(monkeypatch):
    """The application filesystem is the container image. On a laptop that is
    normal; on a container host it is every listener's account, erased on the
    next push.

    Asserted as the relationship rather than as a location, because the suite's
    own fixtures move these stores to a tmp directory - which is on the same
    device here, and so is exactly the case being claimed.
    """
    from fastapi.testclient import TestClient

    import app as appmod

    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    body = TestClient(appmod.app).get("/api/health").json()
    code_device = os.stat(ROOT).st_dev
    checked = 0
    for entry in body["databases"]:
        if entry["persistence"] == "memory":
            continue
        target = entry["path"]
        if not os.path.exists(target):
            target = os.path.dirname(target)
        if os.stat(target).st_dev != code_device:
            continue
        checked += 1
        assert entry["persistence"] == "image", entry
        assert entry["name"] in body["storage"]["ephemeral"], entry
    assert checked, "no database shared a filesystem with the code; nothing was proved"


def test_the_summary_names_what_would_be_lost_rather_than_counting_it(monkeypatch):
    """A number nobody can act on is the shape of report this project keeps
    replacing: the sentence has to say which stores and what to do."""
    from fastapi.testclient import TestClient

    import app as appmod

    monkeypatch.setattr(appmod, "_rate_limit", lambda request: None)
    storage = TestClient(appmod.app).get("/api/health").json()["storage"]
    assert set(storage) == {"durable", "ephemeral", "unknown", "note"}
    assert storage["note"], "an empty sentence is not a report"
    for name in storage["ephemeral"]:
        assert name in storage["note"], f"{name} is at risk and unnamed"


def test_persistence_is_measured_from_the_filesystem_not_from_the_setting():
    """A store pointed at /data with no disk actually mounted is exactly the
    case somebody needs told about, and a configuration check cannot see it."""
    import app as appmod

    same = os.stat(ROOT).st_dev
    assert appmod._persistence_of(str(ROOT), same) == "image"
    assert appmod._persistence_of(str(ROOT), same + 1) == "disk"
    assert appmod._persistence_of(str(ROOT), None) == "unknown"
    assert appmod._persistence_of(str(ROOT / "no-such-directory"), same) == "unknown"
