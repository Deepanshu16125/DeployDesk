#!/bin/sh
set -e
# a fresh volume mounted over data/ has no index yet: build one from whatever docs are there
if [ ! -f "${INDEX_DIR:-data/index}/chunks.jsonl" ] && [ -d data/raw ]; then
  echo "no index found, ingesting data/raw ..."
  python -m deploydesk.cli ingest
fi
exec "$@"
