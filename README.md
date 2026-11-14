# FMHY Rejected Links

A searchable directory of links posted in FMHY's `#remove-sites` Discord
channel — deduplicated, dead-link-filtered, and paired with a short
auto-generated description for each site.

- `site/` — the static website (vanilla HTML/JS, served with Bun)
- `final_links.csv` / `final_links.json` — the curated dataset (live links only, deduped)
- `dead_links.csv` — everything excluded (403/404/timeouts/etc.), kept for reference
- `rejected_sites.csv` — the master dataset (every link ever seen, alive or dead)
- `scripts/export_discord.sh` — pulls new messages from Discord and rebuilds everything above

## Running the site

Requires [Bun](https://bun.sh).

```bash
bun run site/server.ts
# or: npm run site
```

Then open http://localhost:3000.

## Pulling a new export from Discord

This re-fetches only the messages posted since the last export, merges any
new links into the master dataset, and rebuilds the site data — one command.

**One-time setup:**

1. Copy `.env.example` to `.env` and fill in `DISCORD_TOKEN` (see below).
2. That's it — the script downloads its own copy of
   [DiscordChatExporter.Cli](https://github.com/Tyrrrz/DiscordChatExporter)
   and creates a Python virtualenv (installing `requirements.txt`) on first run.

**Getting a Discord token:** open Discord in a browser, open DevTools
(Cmd+Opt+I) → Network tab, reload, click any request to `discord.com/api`,
and copy the `authorization` header. This is your personal account token —
treat it like a password. It's only ever read from `.env`, which is
git-ignored and never committed.

**Run it:**

```bash
npm run export
# or: bash scripts/export_discord.sh
```

This will:

1. Export messages from the channel since the last run (tracked in
   `.last_export_checkpoint`) using DiscordChatExporter.Cli.
2. Fetch title/description for any newly-found links and append them to
   `rejected_sites.csv` / `progress.json`.
3. Rebuild `final_links.csv`, `final_links.json`, and `dead_links.csv` via
   `build_final_dataset.py`.
4. Copy the refreshed data into `site/data.json`.

Restart the site (or just refresh the browser if it's already running) to
see the new links.

## Automated refresh (GitHub Actions)

`.github/workflows/refresh-data.yml` runs `scripts/export_discord.sh` **weekly
(Mondays 06:00 UTC)** and on demand (**Actions → Refresh data → Run workflow**).
If the run changes any data files it pushes a branch
`automation/data-refresh-<timestamp>` and opens a pull request — it never
pushes to the default branch, so you review and merge the refreshed data
yourself.

One-time setup:

1. Add a repository secret `DISCORD_TOKEN` (Settings → Secrets and variables → Actions).
   The run fails with a clear message if it is missing.
2. Enable **Settings → Actions → General → Workflow permissions → "Allow GitHub
   Actions to create and approve pull requests"** (needed for the PR step).
3. Optional: set a repository variable `DISCORD_CHANNEL_ID` to override the default channel.

`.last_export_checkpoint` is committed (it is part of the refresh PR) so each
scheduled run only fetches messages posted since the previous merged refresh.
The checkpoint only advances after the whole pipeline succeeds, so a failed run
can't cause messages to be skipped. Note that a personal-account token is used
by DiscordChatExporter; treat the secret accordingly.

### First-time channel details

- Guild: `956006107564879872`
- Channel: `986617857133649921` (`#🗑 | Remove / remove-sites`)
- Initial full export covered messages up through 2026-08-10 11:49 PM —
  every `npm run export` after that only fetches what's new.

## Manual pipeline (if you need to rebuild from scratch)

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python3 run_overnight.py --input "<path to a Discord HTML export>"
.venv/bin/python3 build_final_dataset.py
cp final_links.json site/data.json
```

`run_overnight.py` resumes safely — it skips any URL already present in
`progress.json`, so re-running it after a partial failure just picks up
where it left off.
