"""Every structured-output schema FAM sends is one the API will accept.

The API rejects an `output_config.format` schema with a 400 if any object in
it leaves `additionalProperties` unset (PROBLEMS.md §130). The category placer
shipped that way and failed on every sweep in production, while every test
passed, because the tests stub the model and never send a schema anywhere.

The list of schemas is **read out of the source**, not written here: a guard
whose subject is enumerated by hand is decorative (§107), and the next schema
somebody adds is exactly the one a hand-written list would miss.
"""
from __future__ import annotations

import importlib
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
USE = re.compile(r'"type":\s*"json_schema",\s*"schema":\s*([A-Z_][A-Z0-9_]*)')


def _schemas():
    found = []
    for path in sorted(ROOT.glob("*.py")):
        for name in USE.findall(path.read_text(encoding="utf-8")):
            module = importlib.import_module(path.stem)
            found.append((f"{path.stem}.{name}", getattr(module, name)))
    return found


def _open_objects(schema, where="$"):
    bad = []
    if isinstance(schema, dict):
        kind = schema.get("type")
        if kind == "object" or (isinstance(kind, list) and "object" in kind):
            if schema.get("additionalProperties") is not False:
                bad.append(where)
        for key, value in schema.items():
            bad += _open_objects(value, f"{where}.{key}")
    elif isinstance(schema, list):
        for i, value in enumerate(schema):
            bad += _open_objects(value, f"{where}[{i}]")
    return bad


def test_the_schemas_are_found():
    names = {name for name, _ in _schemas()}
    # The three in the code today. More is fine; fewer means the pattern above
    # stopped matching and this test stopped checking anything.
    assert {"categories.PLACER_SCHEMA", "episode_intelligence.BRIEF_SCHEMA",
            "stories.STORY_SCHEMA"} <= names


def test_every_object_closes_its_properties():
    problems = {name: _open_objects(schema) for name, schema in _schemas()}
    assert not {k: v for k, v in problems.items() if v}, problems
