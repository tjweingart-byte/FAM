#!/bin/sh
# Called by the Dockerfile. The embedder is optional, so a failure here leaves
# a working FAM that ranks on tags alone and never fails the build.
#
# It is a script rather than an inline RUN because BuildKit prints a RUN's
# own source in the build log, and Render highlights any log line containing
# "WARNING". An inline `|| echo "WARNING: ..."` was therefore flagged on every
# build, including the ones where the install worked - a warning that fires
# on success is one nobody reads when it fires for real. Here the word only
# reaches the log when the install actually failed.
if [ "${FAM_EMBED:-1}" != "1" ]; then
  echo "FAM_EMBED=${FAM_EMBED}: embedding model skipped; ranking on tags alone"
  exit 0
fi
if pip install --no-cache-dir -r requirements-embed.txt \
   && python tools/install_embed_model.py; then
  exit 0
fi
echo "WARNING: embedding model not installed; ranking on tags alone"
exit 0
