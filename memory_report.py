"""Where the server's memory is, asked of the running process (§331).

`/api/health` says how much (`memory`: rss and peak); this says where, for
`/api/admin/memory`. Production sat at ~2.1 GB resident on Postgres while the
same code measured ~310 MB here, and nothing could tell live data from
memory the allocator kept after a large result had been read and dropped
(on Postgres a query's whole result is read before the first row is
returned - `db.Cursor`). Three readings, cheapest first:

* **The allocator** (`allocator()`): glibc's own count of bytes in use and
  bytes it holds free. A large `free_mb` is memory nobody uses that the
  process has not given back.
* **Give it back** (`trim()`): `malloc_trim(0)` and the resident size before
  and after. A large drop is the answer to "leak or held"; it is also safe
  to do at any time.
* **Who holds the rest** (`holders()`): every module of this project, each
  global's reachable size, deduplicated - what is shared is counted once,
  under the first holder found. Bounded by `max_objects` so a census of a
  full process cannot itself run it out of memory; `complete: false` says
  the bound was reached and the numbers are a floor.

Nothing here runs unless an admin asks - except the watch (§333): the
server samples its own size every minute and writes a full report to the
log the first time each boot it crosses each of `WATCH_MB`, because the
state worth seeing is the one before Render restarts it, when nobody is
looking. `all_arenas()` counts every glibc arena (one per thread, up to
eight per core, unless `MALLOC_ARENA_MAX` says fewer), where `allocator()`
sees only the main one.
"""
from __future__ import annotations

import collections
import ctypes
import gc
import os
import re
import sys
import tempfile
import threading
import time
import types
from typing import Optional

_ROOT = os.path.dirname(os.path.abspath(__file__))

#: Things that are the program rather than its data: walking into them walks
#: the whole interpreter.
_SKIP = (types.ModuleType, type, types.FunctionType, types.BuiltinFunctionType,
         types.MethodType, types.CodeType, types.FrameType)


def rss_mb() -> Optional[float]:
    try:
        with open("/proc/self/status", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024, 1)
    except OSError:
        pass
    return None


class _MallInfo2(ctypes.Structure):
    _fields_ = [(name, ctypes.c_size_t) for name in (
        "arena", "ordblks", "smblks", "hblks", "hblkhd", "usmblks",
        "fsmblks", "uordblks", "fordblks", "keepcost")]


def _libc():
    try:
        return ctypes.CDLL("libc.so.6")
    except OSError:
        return None


def allocator() -> dict:
    """glibc's view: in use, held free, and mmapped. Empty off glibc."""
    libc = _libc()
    if libc is None or not hasattr(libc, "mallinfo2"):
        return {}
    libc.mallinfo2.restype = _MallInfo2
    info = libc.mallinfo2()
    mb = lambda n: round(n / 1048576, 1)  # noqa: E731
    return {"heap_mb": mb(info.arena), "in_use_mb": mb(info.uordblks),
            "free_held_mb": mb(info.fordblks), "mmapped_mb": mb(info.hblkhd),
            "note": "main heap only; other threads' arenas count in rss, not here"}


def all_arenas() -> dict:
    """Every arena's totals from `malloc_info`: how many arenas, the memory
    they hold from the system, and how much of it is free. Empty off glibc."""
    libc = _libc()
    if libc is None or not hasattr(libc, "malloc_info"):
        return {}
    libc.fopen.restype = ctypes.c_void_p
    libc.fopen.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
    libc.fclose.argtypes = [ctypes.c_void_p]
    libc.malloc_info.argtypes = [ctypes.c_int, ctypes.c_void_p]
    fd, path = tempfile.mkstemp(prefix="fam-malloc-", suffix=".xml")
    os.close(fd)
    try:
        fp = libc.fopen(path.encode(), b"w")
        if not fp:
            return {}
        try:
            libc.malloc_info(0, fp)
        finally:
            libc.fclose(fp)
        with open(path, encoding="utf-8", errors="replace") as fh:
            xml = fh.read()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    # The totals over every arena follow the last </heap>.
    tail = xml[xml.rfind("</heap>"):] if "</heap>" in xml else xml
    def size(kind: str, typ: str) -> int:
        m = re.search(rf'<{kind} type="{typ}"[^>]*size="(\d+)"', tail)
        return int(m.group(1)) if m else 0
    mb = lambda n: round(n / 1048576, 1)  # noqa: E731
    return {"arenas": xml.count("<heap nr="),
            "held_from_system_mb": mb(size("system", "current")),
            "free_in_arenas_mb": mb(size("total", "fast") + size("total", "rest")),
            "mmapped_mb": mb(size("total", "mmap")),
            "arena_max": os.environ.get("MALLOC_ARENA_MAX") or "default (8 per core)",
            "cpus": os.cpu_count()}


def thread_names(top: int = 12) -> list:
    """Live threads grouped by name, digits dropped - which pools made them."""
    counts: dict = {}
    for t in threading.enumerate():
        name = re.sub(r"[-_ ]?\d+", "", t.name) or "thread"
        counts[name] = counts.get(name, 0) + 1
    return [{"name": k, "count": v} for k, v in
            sorted(counts.items(), key=lambda kv: -kv[1])[:top]]


def trim() -> dict:
    """Hand free memory back to the system, and measure what that did."""
    before = rss_mb()
    gc.collect()
    libc = _libc()
    trimmed = False
    if libc is not None and hasattr(libc, "malloc_trim"):
        libc.malloc_trim(0)
        trimmed = True
    after = rss_mb()
    return {"rss_before_mb": before, "rss_after_mb": after, "trimmed": trimmed,
            "released_mb": (round(before - after, 1)
                            if before is not None and after is not None else None)}


def _is_ours(module) -> bool:
    path = getattr(module, "__file__", None) or ""
    return path.startswith(_ROOT) and "/tests/" not in path


def holders(top: int = 25, max_objects: int = 1_000_000) -> dict:
    """The largest globals of this project's modules, by reachable size."""
    started = time.perf_counter()
    seen: set = set()
    visited = 0
    complete = True
    sizes = []
    for name, module in list(sys.modules.items()):
        if module is None or not _is_ours(module):
            continue
        for attr, value in list(vars(module).items()):
            if isinstance(value, _SKIP) or attr.startswith("__"):
                continue
            total = 0
            stack = [value]
            while stack:
                obj = stack.pop()
                if id(obj) in seen or isinstance(obj, _SKIP):
                    continue
                if visited >= max_objects:
                    complete = False
                    break
                seen.add(id(obj))
                visited += 1
                try:
                    total += sys.getsizeof(obj)
                    stack.extend(gc.get_referents(obj))
                except Exception:  # noqa: BLE001 - an odd object is skipped
                    continue
            if total >= 1048576:
                sizes.append((total, f"{name}.{attr}", type(value).__name__))
            if not complete:
                break
        if not complete:
            break
    sizes.sort(reverse=True)
    return {"largest": [{"name": n, "type": t, "mb": round(s / 1048576, 1)}
                        for s, n, t in sizes[:top]],
            "objects_walked": visited, "complete": complete,
            "seconds": round(time.perf_counter() - started, 2)}


def by_type(top: int = 15) -> list:
    """Container objects the collector tracks, counted by type."""
    counts: dict = {}
    for obj in gc.get_objects():
        name = type(obj).__name__
        n, size = counts.get(name, (0, 0))
        counts[name] = (n + 1, size + sys.getsizeof(obj))
    ranked = sorted(counts.items(), key=lambda kv: -kv[1][1])[:top]
    return [{"type": k, "count": n, "mb": round(s / 1048576, 1)} for k, (n, s) in ranked]


def report(trim_first: bool = True, census: bool = False) -> dict:
    out = {"rss_mb": rss_mb(), "allocator": allocator(),
           "all_arenas": all_arenas(),
           "threads": len(os.listdir("/proc/self/task")) if os.path.isdir("/proc/self/task") else None,
           "thread_names": thread_names()}
    if trim_first:
        out["trim"] = trim()
        out["allocator_after_trim"] = allocator()
    if census:
        out["holders"] = holders()
        out["by_type"] = by_type()
    return out


# --- the watch (§333) ------------------------------------------------------

#: Sizes at which the watch writes a full report, once each per boot.
WATCH_MB = (800, 1200, 1600)
#: One sample a minute for the last six hours.
SAMPLES: collections.deque = collections.deque(maxlen=360)
#: The reports the watch wrote this boot, newest last.
SNAPSHOTS: list = []
_CROSSED: set = set()


def watch_step(now: Optional[float] = None, rss: Optional[float] = None) -> Optional[dict]:
    """One sample. Returns a report the first time `rss` crosses one of
    `WATCH_MB` this boot (the caller logs it), else None. No trim: the
    point is to see the size as it was."""
    rss = rss_mb() if rss is None else rss
    if rss is None:
        return None
    SAMPLES.append((round(time.time() if now is None else now), rss))
    crossed = [mb for mb in WATCH_MB if rss >= mb and mb not in _CROSSED]
    if not crossed:
        return None
    _CROSSED.update(crossed)
    snap = {"at": round(time.time()), "crossed_mb": max(crossed),
            **report(trim_first=False, census=True)}
    SNAPSHOTS.append(snap)
    del SNAPSHOTS[:-len(WATCH_MB)]
    return snap


def watch_state() -> dict:
    return {"thresholds_mb": list(WATCH_MB), "crossed_mb": sorted(_CROSSED),
            "samples": list(SAMPLES), "snapshots": list(SNAPSHOTS)}
