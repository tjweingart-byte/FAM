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

Nothing here runs unless an admin asks.
"""
from __future__ import annotations

import ctypes
import gc
import os
import sys
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
           "threads": len(os.listdir("/proc/self/task")) if os.path.isdir("/proc/self/task") else None}
    if trim_first:
        out["trim"] = trim()
        out["allocator_after_trim"] = allocator()
    if census:
        out["holders"] = holders()
        out["by_type"] = by_type()
    return out
