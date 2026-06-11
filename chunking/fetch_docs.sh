#!/usr/bin/env bash
# Prototype helper: pull ONLY the SAP AI Core seed PDF into chunking/docs/, which
# the chunk_*.py prototypes read by default. Kept out of git (SAP © content).
#
# The full, canonical corpus retrieval lives in ../corpus/ — see ../CORPUS.md.
# To secure the whole 3-doc corpus + manifest:  ../corpus/fetch.sh
set -e
mkdir -p docs
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36"
URL="https://help.sap.com/doc/c31b38b32a5d4e07a4488cb0f8bb55d9/CLOUD/en-US/f17fa8568d0448c685f2a0301061a6ee.pdf"
curl -sL -A "$UA" "$URL" -o docs/sap-ai-core.pdf
echo "saved docs/sap-ai-core.pdf ($(du -h docs/sap-ai-core.pdf | cut -f1))"
