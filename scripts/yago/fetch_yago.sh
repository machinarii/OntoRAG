#!/usr/bin/env bash
# YAGO 4.0 T-Box files for the LightRAG taxonomy layer.
#
# As of the YAGO-4.0 switch this script no longer downloads anything by default.
# The canonical files live committed at /Users/jin/OntoRAG/yago/ and their
# sha256s are pinned in lightrag/taxonomy/manifest.py. This script is kept as a
# pointer so anyone landing on it knows where the files originally came from
# and how to re-fetch them if the local copies are lost or corrupted.

set -euo pipefail

YAGO_DIR="${YAGO_DIR:-/Users/jin/OntoRAG/yago}"
BASE_URL="https://yago-knowledge.org/data/yago4/full/2020-02-24"
FILES=(yago-wd-class.nt yago-wd-schema.nt yago-wd-shapes.nt)

cat <<EOF
YAGO 4.0 T-Box files are expected at:
  ${YAGO_DIR}/yago-wd-class.nt    (≈60 MB — class definitions, subClassOf, labels)
  ${YAGO_DIR}/yago-wd-schema.nt   (≈340 KB — schema-level properties)
  ${YAGO_DIR}/yago-wd-shapes.nt   (≈210 KB — SHACL shapes)

Pinned SHA256s live in lightrag/taxonomy/manifest.py. Verify with:
  python -c 'from lightrag.taxonomy.manifest import verify_yago_files; verify_yago_files()'

If files are missing or drifted, re-download from:
  ${BASE_URL}/yago-wd-class.nt.gz
  ${BASE_URL}/yago-wd-schema.nt.gz
  ${BASE_URL}/yago-wd-shapes.nt.gz

Then gunzip into ${YAGO_DIR}/.
EOF

# Optional: pass --fetch to actually run the curl commands (idempotent — skips
# files already present at non-zero size).
if [[ "${1:-}" == "--fetch" ]]; then
  mkdir -p "${YAGO_DIR}"
  for fname in "${FILES[@]}"; do
    target="${YAGO_DIR}/${fname}"
    if [[ -s "${target}" ]]; then
      echo "Already present: ${target}"
      continue
    fi
    echo "Downloading ${BASE_URL}/${fname}.gz → ${target}"
    curl --fail --location --output "${target}.gz" "${BASE_URL}/${fname}.gz"
    gunzip "${target}.gz"
  done
fi
