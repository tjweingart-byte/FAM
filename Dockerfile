# Runs FAM anywhere that takes a container: Render, Fly, Railway, Cloud Run.
# Kept deliberately plain - there is no build step, because the interface is a
# static file and the server is one Python process.
FROM python:3.12-slim

# espeak-ng is a development engine only, reachable through TTS_ENGINE and
# never selected by production - nothing falls back to it. It is installed so
# that a container can be used for local work; an image that is meant to speak
# needs a GPU and requirements-chatterbox.txt, and without those FAM reports
# `interim: true` and plays a placeholder tone rather than a worse voice.
RUN apt-get update \
 && apt-get install -y --no-install-recommends espeak-ng ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# Both files. `exa_py` is declared only in requirements-exa.txt, and
# RESEARCH_BACKEND defaults to `exa` (config.py DEFAULT_RESEARCH_BACKEND), so
# without it this image starts with research already broken: `research.diagnose`
# reports "exa_py is not installed" and every researched episode raises
# ResearchUnavailable rather than searching another way. Dockerfile.gpu has
# always installed both; this image was the one left behind.
#
# It costs nothing to carry: exa-py is pure Python, no torch, no CUDA.
COPY requirements.txt requirements-exa.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-exa.txt

COPY . .

# Every database lives on a mounted disk where the host provides one, so they
# survive a redeploy. Without a disk they are ephemeral and every deploy is a
# fresh start - which is fine for a preview and not for real listeners.
#
# Every one of them is named here on purpose, and the list has now been
# incomplete twice. The first time it was social and attachments, written to
# the image's WORKDIR instead of the disk, so every redeploy silently
# discarded every listener's name, handle and echo. The second time it was
# messages, saved, shares and quotas - four stores added after this list was
# written - so a deployment lost every conversation, every saved episode and
# every share link on each push while the accounts beside them survived.
#
# The guard that was supposed to catch it did not, and that is the part worth
# remembering: `tests/test_data_paths.py` compared this file against its own
# `STORES` list, which is also maintained by hand, so a store missing from
# both looked consistent. It now derives the list from the modules that call
# `data_path`, which cannot be forgotten because it is not written down twice.
ENV CACHE_PATH=/data/scripts.db \
    MYFAM_DB=/data/myfam.db \
    MIXES_DB=/data/mixes.db \
    SOCIAL_DB=/data/social.db \
    ATTACHMENTS_PATH=/data/attachments.db \
    ACCOUNTS_DB=/data/accounts.db \
    PREFS_DB=/data/preferences.db \
    METERING_DB=/data/metering.db \
    MESSAGES_DB=/data/messages.db \
    SAVED_DB=/data/saved.db \
    SHARES_DB=/data/shares.db \
    QUOTAS_DB=/data/quotas.db \
    PORT=8000
RUN mkdir -p /data

EXPOSE 8000
CMD ["sh", "-c", "python -m uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}"]
