"""Install the local sentence embedder, and prove it produces a vector.

    python tools/install_embed_model.py            # into ~/.fam/embed
    python tools/install_embed_model.py --check    # only say whether it works

What it installs is all-MiniLM-L6-v2 as ONNX: 384 dimensions, ~90 MB, ~10 ms a
sentence on one CPU core, no torch. It lives in `~/.fam/embed` (or
`FAM_EMBED_MODEL`) for the reason the voice models live in `~/.fam/voices`: a
model downloaded once should be found by every later copy of the app.

**Where it comes from, and why there.** The obvious source is Hugging Face,
and the build container's network policy refuses it. Chroma publishes the same
export as a single tarball on S3, which is reachable from here and from a
normal deployment, so that is the default. The archive's SHA-256 is pinned:
a model file is code that runs on the ranking path, and one that changed under
the same URL is refused rather than installed. `--url` and `--sha256` override
both for a mirror.

**It is not done until a sentence has been encoded** (CLAUDE.md, "verify, do
not inspect"). Files on disk are the cheaper question; this answers the real
one, and exits non-zero if the model will not load or produces a vector that
is not unit length - which is exactly the state `embeddings.py` would
otherwise fall back from quietly.

Needs `pip install -r requirements-embed.txt` first.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import os
import sys
import tarfile
import urllib.request
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import embeddings  # noqa: E402

DEFAULT_URL = ("https://chroma-onnx-models.s3.amazonaws.com/"
               "all-MiniLM-L6-v2/onnx.tar.gz")
#: Of the archive at DEFAULT_URL, as downloaded for §131.
DEFAULT_SHA256 = "913d7300ceae3b2dbc2c50d1de4baacab4be7b9380491c27fab7418616a16ec3"
#: The two files `embeddings._OnnxEmbedder` reads. Everything else in the
#: archive (vocab.txt, the configs) is for other runtimes.
WANTED = ("model.onnx", "tokenizer.json")


def check() -> int:
    """Encode two sentences and say what happened. 0 means it works."""
    if not embeddings.model_installed():
        print(f"No model in {embeddings.model_dir()}.")
        return 1
    encoder = embeddings.semantic_encoder()
    if encoder is None:
        print("The files are there and the model did not load:")
        print("  " + embeddings.describe()["error"])
        print("  pip install -r requirements-embed.txt")
        return 1
    a, b, c = encoder.encode_many([
        "who won the game last night",
        "the result of yesterday's match",
        "how do vaccines work",
    ])
    norm = sum(x * x for x in a) ** 0.5
    close = embeddings.cosine(a, b)
    far = embeddings.cosine(a, c)
    print(f"Model: {embeddings.model_dir()} ({len(a)} dims)")
    print(f"  unit length       {norm:.4f}")
    print(f"  related pair      {close:.3f}")
    print(f"  unrelated pair    {far:.3f}")
    if abs(norm - 1.0) > 1e-3 or close <= far:
        print("That is not a working sentence embedder.")
        return 1
    print("Working.")
    return 0


def install(url: str, sha256: str) -> int:
    target = embeddings.model_dir()
    print(f"Downloading {url}")
    with urllib.request.urlopen(url, timeout=120) as response:
        data = response.read()
    digest = hashlib.sha256(data).hexdigest()
    if sha256 and digest != sha256:
        print(f"Refusing it: SHA-256 is {digest}, expected {sha256}.")
        return 1
    target.mkdir(parents=True, exist_ok=True)
    found = set()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for member in archive.getmembers():
            name = Path(member.name).name
            if name in WANTED and member.isfile():
                extracted = archive.extractfile(member)
                if extracted is None:
                    continue
                (target / name).write_bytes(extracted.read())
                found.add(name)
    missing = set(WANTED) - found
    if missing:
        print("The archive did not contain " + ", ".join(sorted(missing)) + ".")
        return 1
    print(f"Installed into {target}.")
    return check()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true",
                        help="only test the model already installed")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--sha256", default=DEFAULT_SHA256,
                        help="expected archive digest ('' to skip, for a mirror)")
    args = parser.parse_args()
    if args.check:
        return check()
    return install(args.url, args.sha256)


if __name__ == "__main__":
    sys.exit(main())
