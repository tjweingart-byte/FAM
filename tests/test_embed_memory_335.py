"""The embedding model's memory (§335): one pass at a time, never more than
`MAX_BATCH` texts in a pass, two threads, no fixed padding."""
from __future__ import annotations

import importlib.util
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import embeddings  # noqa: E402

np = pytest.importorskip("numpy")


class _Encoding:
    def __init__(self, n):
        self.ids = list(range(1, n + 1))
        self.attention_mask = [1] * n


class _Tokenizer:
    def encode(self, text):
        return _Encoding(min(len(text.split()), 128))


class _Input:
    def __init__(self, name):
        self.name = name


class _Session:
    """Records the widest batch and the most passes running at once."""

    def __init__(self):
        self.batches = []
        self.running = 0
        self.most_at_once = 0
        self._lock = threading.Lock()

    def get_inputs(self):
        return [_Input("input_ids"), _Input("attention_mask")]

    def run(self, _outputs, feed):
        with self._lock:
            self.running += 1
            self.most_at_once = max(self.most_at_once, self.running)
        time.sleep(0.01)
        ids = feed["input_ids"]
        self.batches.append(ids.shape[0])
        with self._lock:
            self.running -= 1
        return [np.ones((ids.shape[0], ids.shape[1], 4), dtype=np.float32)]


def _embedder():
    e = embeddings._OnnxEmbedder.__new__(embeddings._OnnxEmbedder)
    e.tokenizer = _Tokenizer()
    e.session = _Session()
    e.inputs = {"input_ids", "attention_mask"}
    e.dims = 4
    e._run_lock = threading.Lock()
    return e


def test_a_large_call_is_split_into_passes_of_max_batch():
    e = _embedder()
    out = e.encode_many([f"text {i}" for i in range(40)])
    assert len(out) == 40
    assert e.session.batches == [16, 16, 8]


def test_concurrent_callers_take_turns():
    e = _embedder()
    threads = [threading.Thread(target=e.encode_many, args=([f"t {i}"] * 6,))
               for i in range(12)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(e.session.batches) == 12
    assert e.session.most_at_once == 1


def test_threads_default_to_two_and_can_be_set(monkeypatch):
    monkeypatch.delenv("FAM_EMBED_THREADS", raising=False)
    assert embeddings.embed_threads() == 2
    monkeypatch.setenv("FAM_EMBED_THREADS", "4")
    assert embeddings.embed_threads() == 4
    monkeypatch.setenv("FAM_EMBED_THREADS", "nonsense")
    assert embeddings.embed_threads() == 2
    monkeypatch.setenv("FAM_EMBED_THREADS", "0")
    assert embeddings.embed_threads() == 1


def _real_model():
    """The model where the Docker image or the installer put it; conftest
    points `model_dir()` away from both on purpose."""
    import pwd
    from pathlib import Path
    for where in (Path("/opt/fam/embed"),
                  Path(pwd.getpwuid(os.getuid()).pw_dir) / ".fam" / "embed"):
        if (where / "model.onnx").exists() and (where / "tokenizer.json").exists():
            return where
    return None


@pytest.mark.skipif(_real_model() is None or importlib.util.find_spec("onnxruntime") is None,
                    reason="no embedding model installed")
def test_the_real_model_pads_to_the_batch_not_to_128():
    e = embeddings._OnnxEmbedder(_real_model())
    assert len(e.tokenizer.encode("a short title").ids) < 128
    assert e.session.get_session_options().intra_op_num_threads == embeddings.embed_threads()


def test_every_way_of_starting_the_server_runs_one_process():
    """§336: uvicorn reads WEB_CONCURRENCY when --workers is not given, and
    production ran two processes - every background job twice."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for name in ("Dockerfile", "start.sh", "run.sh"):
        text = open(os.path.join(here, name)).read()
        lines = [line for line in text.splitlines()
                 if "uvicorn app:app" in line and not line.lstrip().startswith("#")]
        assert lines, name
        assert all("--workers 1" in line for line in lines), name
