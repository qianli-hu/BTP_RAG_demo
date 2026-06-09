#!/usr/bin/env bash
# Download the SAP AI Core guide PDF (SAP Help Portal) into docs/.
# Kept out of git (see .gitignore) — SAP-copyright content, fetch locally.
set -e
mkdir -p docs
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36"
URL="https://help.sap.com/doc/c31b38b32a5d4e07a4488cb0f8bb55d9/CLOUD/en-US/f17fa8568d0448c685f2a0301061a6ee.pdf"
curl -sL -A "$UA" "$URL" -o docs/sap-ai-core.pdf
echo "saved docs/sap-ai-core.pdf ($(du -h docs/sap-ai-core.pdf | cut -f1))"
