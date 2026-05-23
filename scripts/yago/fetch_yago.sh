#!/usr/bin/env bash
# Download YAGO 4.5 schema + taxonomy N-Triples for the LightRAG taxonomy layer.
#
# YAGO 4.5 publishes its data at https://yago-knowledge.org/downloads/yago-4-5
# We only need the T-Box (schema + taxonomy), not the entity facts (A-Box).
#
# Usage: bash scripts/yago/fetch_yago.sh [version]
# Default version is the pinned release below. Files land in
# data/yago/<version>/ and the script is idempotent (re-running skips files
# that already exist with non-zero size).

set -euo pipefail

VERSION="${1:-2024-02-29}"
BASE_URL="https://yago-knowledge.org/data/yago4.5/${VERSION}"
TARGET_DIR="data/yago/${VERSION}"

mkdir -p "${TARGET_DIR}"

FILES=(
  "yago-schema.nt"
  "yago-taxonomy.nt"
)

for fname in "${FILES[@]}"; do
  target="${TARGET_DIR}/${fname}"
  if [[ -s "${target}" ]]; then
    echo "Already present: ${target} ($(wc -c < "${target}") bytes)"
    continue
  fi
  echo "Downloading ${BASE_URL}/${fname} -> ${target}"
  curl --fail --location --output "${target}" "${BASE_URL}/${fname}"
  echo "Downloaded $(wc -c < "${target}") bytes"
done

echo
echo "YAGO ${VERSION} files in ${TARGET_DIR}:"
ls -lh "${TARGET_DIR}"
echo
echo "Next: python scripts/yago/build_yago_taxonomy.py --files ${TARGET_DIR}/*.nt --working-dir ./rag_storage"
