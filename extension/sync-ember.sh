#!/usr/bin/env bash
# Copy Ember tokens and fonts from the web UI, so the extension matches it.
# Run after scripts/gen_tokens_css.py regenerates web/css/tokens.css.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
cp "$here/../web/css/tokens.css" "$here/css/tokens.css"
cp "$here"/../web/fonts/*.woff2 "$here/../web/fonts/OFL.txt" "$here/fonts/"
echo "Synced Ember tokens and fonts into extension/."
