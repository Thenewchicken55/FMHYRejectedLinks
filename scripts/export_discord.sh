#!/usr/bin/env bash
# Pulls new messages from the FMHY remove-sites Discord channel since the
# last export, fetches metadata for any new links, and rebuilds the site.
#
# Usage: npm run export   (or: bash scripts/export_discord.sh)
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

# Credentials come from .env locally, or from real environment variables
# (e.g. a GitHub Actions secret) when there is no .env file.
if [ -f .env ]; then
  set -a
  source .env
  set +a
fi

: "${DISCORD_TOKEN:?DISCORD_TOKEN is not set. Copy .env.example to .env and fill it in (or export it in the environment).}"
: "${DISCORD_CHANNEL_ID:?DISCORD_CHANNEL_ID is not set (see .env.example).}"

DCE_DIR="tools/DiscordChatExporter.Cli"
DCE_BIN="$DCE_DIR/DiscordChatExporter.Cli"
DCE_VERSION="2.48"

# ── Download DiscordChatExporter.Cli if missing ─────────────────────────────
if [ ! -x "$DCE_BIN" ]; then
  echo "DiscordChatExporter.Cli not found — downloading v$DCE_VERSION..."
  OS="$(uname -s)"
  ARCH="$(uname -m)"
  case "$OS-$ARCH" in
    Darwin-arm64) ASSET="osx-arm64" ;;
    Darwin-x86_64) ASSET="osx-x64" ;;
    Linux-aarch64|Linux-arm64) ASSET="linux-arm64" ;;
    Linux-x86_64) ASSET="linux-x64" ;;
    *) echo "ERROR: unsupported platform $OS-$ARCH. Download manually from" \
            "https://github.com/Tyrrrz/DiscordChatExporter/releases" >&2
       exit 1 ;;
  esac
  mkdir -p "$DCE_DIR"
  TMP_ZIP="$(mktemp)"
  curl -fsSL -o "$TMP_ZIP" \
    "https://github.com/Tyrrrz/DiscordChatExporter/releases/download/${DCE_VERSION}/DiscordChatExporter.Cli.${ASSET}.zip"
  unzip -o -q "$TMP_ZIP" -d "$DCE_DIR"
  rm -f "$TMP_ZIP"
  chmod +x "$DCE_BIN"
  echo "Installed to $DCE_BIN"
fi

# ── Figure out the export window ────────────────────────────────────────────
CHECKPOINT_FILE=".last_export_checkpoint"
if [ -f "$CHECKPOINT_FILE" ]; then
  AFTER="$(cat "$CHECKPOINT_FILE")"
else
  # First incremental export after the initial full export.
  AFTER="2026-08-10 23:49"
fi

NOW="$(date -u +"%Y-%m-%dT%H:%M:%S")"
TIMESTAMP="$(date +"%Y%m%d_%H%M%S")"
mkdir -p exports
OUTPUT_HTML="exports/export_${TIMESTAMP}.html"

echo "Exporting channel $DISCORD_CHANNEL_ID (messages after: $AFTER)..."
"$DCE_BIN" export \
  -t "$DISCORD_TOKEN" \
  -c "$DISCORD_CHANNEL_ID" \
  --after "$AFTER" \
  -f HtmlDark \
  -o "$OUTPUT_HTML"

if [ ! -s "$OUTPUT_HTML" ]; then
  echo "No export produced — nothing new to fetch."
  exit 0
fi

echo "Export saved to $OUTPUT_HTML"

# ── Python deps (venv keeps this off the system-managed interpreter) ───────
if [ ! -x ".venv/bin/python3" ]; then
  echo "Setting up Python virtualenv..."
  python3 -m venv .venv
  .venv/bin/pip install --quiet --upgrade pip
  .venv/bin/pip install --quiet -r requirements.txt
fi
PY=".venv/bin/python3"

# ── Fetch metadata for any new links, merged into the master CSV ───────────
echo "Fetching link metadata (resumes/appends into rejected_sites.csv)..."
"$PY" run_overnight.py --input "$OUTPUT_HTML"

# ── Rebuild the curated dataset + site data ─────────────────────────────────
echo "Rebuilding final_links.csv/json..."
"$PY" build_final_dataset.py
cp final_links.json site/data.json

# Only advance the checkpoint once everything above succeeded, so a failed
# run never causes messages to be skipped on the next attempt.
echo "$NOW" > "$CHECKPOINT_FILE"

echo "Done. Run 'npm run site' (or: bun run site/server.ts) to preview locally."
