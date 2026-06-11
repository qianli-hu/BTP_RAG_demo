#!/usr/bin/env bash
# Secure the BTP_RAG corpus into corpus/raw/ (gitignored — SAP © content).
# Corpus is defined in ../CORPUS.md. Reproducible: re-run any time.
#
#   doc 1  SAP AI Core (+ Gen AI Hub)   PDF   -> corpus/raw/sap-ai-core.pdf
#   doc 3  SAP AI Launchpad             PDF   -> corpus/raw/sap-ai-launchpad.pdf
#   doc 2  HANA Cloud Vector Engine     HTML  -> python3 crawl_hana_vector.py
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
RAW="$HERE/raw"
mkdir -p "$RAW"
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36"

fetch_pdf () {  # name  url
  curl -fsSL -A "$UA" "$2" -o "$RAW/$1"
  echo "  ok  $1  ($(du -h "$RAW/$1" | cut -f1))"
}

echo "doc 1/3  SAP AI Core ..."
fetch_pdf sap-ai-core.pdf \
  "https://help.sap.com/doc/c31b38b32a5d4e07a4488cb0f8bb55d9/CLOUD/en-US/f17fa8568d0448c685f2a0301061a6ee.pdf"

echo "doc 3/3  SAP AI Launchpad ..."
fetch_pdf sap-ai-launchpad.pdf \
  "https://help.sap.com/doc/5945759df2d34b69b681c53bb2dd7b9f/CLOUD/en-US/038a6194f65c4ef68885f6f16360dbc4.pdf"

echo "doc 2/3  SAP HANA Cloud Vector Engine Guide (HTML crawl) ..."
python3 "$HERE/crawl_hana_vector.py"

echo "building MANIFEST.json ..."
python3 "$HERE/build_manifest.py"
echo "corpus secured -> $RAW"
