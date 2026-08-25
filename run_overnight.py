#!/usr/bin/env python3
"""
FMHY Rejected Links — Overnight Fetch Runner
Resumes from progress.json, fetches all remaining links in batches,
writes CSV after each batch, and logs progress.

Usage:
    python3 run_overnight.sh           # run in foreground
    nohup python3 run_overnight.py &   # run in background (overnight)

Press Ctrl+C to stop safely — progress is saved every batch.
"""

import csv
import json
import os
import re
import signal
import sys
import time
import warnings
warnings.filterwarnings('ignore', message='.*urllib3.*')
warnings.filterwarnings('ignore', message='.*NotOpenSSLWarning.*')
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urlparse

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("Installing dependencies...")
    os.system("pip3 install beautifulsoup4 requests")
    import requests
    from bs4 import BeautifulSoup

# ── Config ──────────────────────────────────────────────────────────────────
DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(DIR)

HTML_FILE = "FMHY - 🗑 _ Remove - remove-sites [986617857133649921].html"
OUTPUT_CSV = "rejected_sites.csv"
PROGRESS_JSON = "progress.json"
LOG_FILE = f"overnight_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
TOTAL_EXPECTED = 25280  # known unique links from prior parse

BATCH_SIZE = 500        # links per batch before writing CSV
MAX_WORKERS = 25        # concurrent fetches
FETCH_TIMEOUT = 12      # seconds per request
BATCH_PAUSE = 1         # seconds between batches

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

SKIP_DOMAINS = {
    "cdn.discordapp.com", "media.discordapp.net",
    "cdn.discord.com", "images-ext-1.discordapp.net",
    "discord.com", "discord.gg", "discordapp.com",
    "i.imgur.com", "imgur.com",
    "cdnjs.cloudflare.com", "cdn.jsdelivr.net",
    "fonts.googleapis.com", "fonts.gstatic.com",
    "unpkg.com", "raw.githubusercontent.com",
}
SKIP_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".gifv", ".webp",
                   ".svg", ".ico", ".woff2", ".woff", ".ttf", ".mp4",
                   ".mp3", ".zip", ".rar", ".7z", ".pdf", ".exe"}

# ── Globals ─────────────────────────────────────────────────────────────────
shutdown_requested = False


def log(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def handle_signal(sig, frame):
    global shutdown_requested
    shutdown_requested = True
    log("⚠ Ctrl+C received — finishing current batch then exiting...")


signal.signal(signal.SIGINT, handle_signal)
signal.signal(signal.SIGTERM, handle_signal)


# ── HTML Parse ──────────────────────────────────────────────────────────────
def parse_html(filepath):
    log(f"Parsing {filepath}...")
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    blocks = re.split(
        r'class=chatlog__message-container data-message-id=', content
    )
    blocks = blocks[1:]

    messages = []
    link_re = re.compile(r'href="(https?://[^"]+)"')

    for block in blocks:
        author_m = re.search(r'class=chatlog__author[^>]*>([^<]+)<', block)
        ts_m = re.search(r'class=chatlog__timestamp[^>]*>([^<]+)<', block)
        text_m = re.search(r'chatlog__markdown-preserve[^>]*>(.*?)</span>', block, re.DOTALL)
        if not text_m:
            text_m = re.search(r'chatlog__content[^>]*>(.*?)</div>', block, re.DOTALL)

        links = link_re.findall(block)
        context = ""
        if text_m:
            raw = re.sub(r'<[^>]+>', ' ', text_m.group(1)[:500]).strip()
            context = re.sub(r'\s+', ' ', raw)[:200]

        if links:
            messages.append({
                "author": author_m.group(1).strip() if author_m else "unknown",
                "timestamp": ts_m.group(1).strip() if ts_m else "unknown",
                "links": links,
                "context": context,
            })

    log(f"Parsed {len(messages)} messages")
    return messages


def extract_links(messages):
    seen = {}
    for msg in messages:
        for url in msg["links"]:
            parsed = urlparse(url)
            if parsed.hostname and parsed.hostname in SKIP_DOMAINS:
                continue
            if any(parsed.path.lower().endswith(e) for e in SKIP_EXTENSIONS):
                continue
            if not url.startswith("http"):
                continue

            norm = url.rstrip("/")
            if norm not in seen:
                seen[norm] = {
                    "url": norm,
                    "author": msg["author"],
                    "timestamp": msg["timestamp"],
                    "context": msg["context"],
                    "mentioned_by": [msg["author"]],
                }
            elif msg["author"] not in seen[norm]["mentioned_by"]:
                seen[norm]["mentioned_by"].append(msg["author"])

    unique = list(seen.values())
    log(f"Found {len(unique)} unique external links")
    return unique


# ── Fetching ────────────────────────────────────────────────────────────────
def fetch_one(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=FETCH_TIMEOUT,
                            allow_redirects=True)
        status = resp.status_code
        final_url = resp.url

        if status != 200:
            return {"status": status, "final_url": final_url, "title": "",
                    "description": "", "body": f"HTTP {status}",
                    "error": f"HTTP {status}"}

        ct = resp.headers.get("Content-Type", "")
        if "text/html" not in ct:
            return {"status": status, "final_url": final_url, "title": "",
                    "description": "", "body": f"Non-HTML: {ct[:60]}",
                    "error": ""}

        soup = BeautifulSoup(resp.text, "html.parser")
        title = ""
        if soup.title and soup.title.string:
            title = soup.title.string.strip()[:200]
        og = soup.find("meta", property="og:title")
        if og:
            title = og.get("content", title)[:200]

        desc = ""
        md = soup.find("meta", attrs={"name": "description"})
        if md:
            desc = md.get("content", "")[:300]
        ogd = soup.find("meta", property="og:description")
        if ogd:
            desc = ogd.get("content", desc)[:300]

        for t in soup(["script", "style", "noscript"]):
            t.decompose()
        body = soup.get_text(" ", strip=True)[:800]

        return {"status": status, "final_url": final_url, "title": title,
                "description": desc, "body": body, "error": ""}

    except requests.exceptions.Timeout:
        return {"status": 0, "final_url": url, "title": "", "description": "",
                "body": "", "error": "Timeout"}
    except requests.exceptions.SSLError:
        return {"status": 0, "final_url": url, "title": "", "description": "",
                "body": "", "error": "SSL Error"}
    except requests.exceptions.ConnectionError as e:
        return {"status": 0, "final_url": url, "title": "", "description": "",
                "body": "", "error": f"ConnErr: {str(e)[:80]}"}
    except Exception as e:
        return {"status": 0, "final_url": url, "title": "", "description": "",
                "body": "", "error": str(e)[:120]}


# ── Progress / CSV ──────────────────────────────────────────────────────────
def load_progress():
    if os.path.exists(PROGRESS_JSON):
        try:
            with open(PROGRESS_JSON, "r") as f:
                return json.load(f)
        except json.JSONDecodeError:
            log("⚠ progress.json corrupted — attempting recovery...")
            return recover_progress()
    return []


def recover_progress():
    with open(PROGRESS_JSON, "r") as f:
        content = f.read()
    objects = []
    i = 0
    while i < len(content):
        if content[i] == '{':
            depth, start = 0, i
            while i < len(content):
                if content[i] == '{': depth += 1
                elif content[i] == '}':
                    depth -= 1
                    if depth == 0:
                        try:
                            objects.append(json.loads(content[start:i+1]))
                        except: pass
                        break
                i += 1
        i += 1
    if objects:
        save_progress(objects)
        log(f"  Recovered {len(objects)} records")
    return objects


def save_progress(data):
    tmp = PROGRESS_JSON + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, PROGRESS_JSON)


def clean_context(text):
    """Strip HTML tags (complete and truncated) and normalize whitespace."""
    if not text:
        return ""
    clean = text.replace('\x00', '')
    clean = re.sub(r'<[^>]*>', ' ', clean)   # complete tags
    clean = re.sub(r'<[^>]*$', ' ', clean)    # truncated tags at end
    clean = re.sub(r'\s+', ' ', clean).strip()
    return clean[:300]


def generate_summary(record):
    """Generate a one-line summary from title + description + body."""
    parts = []
    if record.get("title"):
        parts.append(record["title"].strip())
    if record.get("description"):
        desc = record["description"].strip()
        if desc and desc != parts[0] if parts else True:
            parts.append(desc[:200])
    if not parts and record.get("body"):
        # Use first sentence of body
        body = record["body"].strip()
        first_sentence = re.split(r'[.!?]\s', body)
        if first_sentence:
            parts.append(first_sentence[0][:200])
    summary = " — ".join(parts) if parts else ""
    return summary[:400]


def write_csv(data):
    if not data:
        return
    fields = ["url", "title", "description", "body", "status",
              "final_url", "author", "timestamp", "context",
              "also_mentioned_by", "error", "summary"]
    tmp = OUTPUT_CSV + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in data:
            row = {k: r.get(k, "") for k in fields}
            row["context"] = clean_context(row["context"])
            row["summary"] = generate_summary(r)
            # Strip NUL from all fields
            for k in row:
                if isinstance(row[k], str):
                    row[k] = row[k].replace('\x00', '')
            w.writerow(row)
    os.replace(tmp, OUTPUT_CSV)


# ── Main Loop ───────────────────────────────────────────────────────────────
def main():
    log("╔══════════════════════════════════════════════════╗")
    log("║  FMHY Overnight Link Fetcher                    ║")
    log("╚══════════════════════════════════════════════════╝")

    if not os.path.exists(HTML_FILE):
        log(f"ERROR: {HTML_FILE} not found")
        sys.exit(1)

    # Parse links (skip if we already have enough progress)
    results = load_progress()
    done_urls = {r["url"] for r in results}

    if len(done_urls) < 100:
        log("Parsing HTML to extract links...")
        messages = parse_html(HTML_FILE)
        all_links = extract_links(messages)
    else:
        # Re-parse to get the full link list for filtering
        log(f"Resuming: {len(done_urls)} already fetched")
        messages = parse_html(HTML_FILE)
        all_links = extract_links(messages)

    remaining = [l for l in all_links if l["url"] not in done_urls]
    log(f"Remaining to fetch: {len(remaining)}")

    if not remaining:
        log("Nothing to fetch — all done!")
        write_csv(results)
        print_summary(results)
        return

    # Fetch in batches
    total_done = len(done_urls)
    total_ok = sum(1 for r in results if not r.get("error"))
    total_fail = total_done - total_ok
    start_time = time.time()
    batch_num = 0

    for batch_start in range(0, len(remaining), BATCH_SIZE):
        if shutdown_requested:
            break

        batch_num += 1
        batch = remaining[batch_start:batch_start + BATCH_SIZE]
        log(f"── Batch {batch_num} | fetching {len(batch)} links "
            f"({total_done}/{TOTAL_EXPECTED} done) ──")

        batch_ok = 0
        batch_fail = 0

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            future_map = {executor.submit(fetch_one, l["url"]): l
                          for l in batch}

            for future in as_completed(future_map):
                if shutdown_requested:
                    executor.shutdown(wait=False, cancel_futures=True)
                    break

                link = future_map[future]
                try:
                    result = future.result()
                except Exception as e:
                    result = {"status": 0, "final_url": link["url"],
                              "title": "", "description": "", "body": "",
                              "error": str(e)[:120]}

                entry = {
                    "url": link["url"],
                    "title": result.get("title", ""),
                    "description": result.get("description", ""),
                    "body": result.get("body", ""),
                    "status": result.get("status", 0),
                    "final_url": result.get("final_url", link["url"]),
                    "author": link["author"],
                    "timestamp": link["timestamp"],
                    "context": link["context"],
                    "also_mentioned_by": ", ".join(
                        link.get("mentioned_by", [])),
                    "error": result.get("error", ""),
                }
                results.append(entry)

                if result.get("error"):
                    batch_fail += 1
                    total_fail += 1
                else:
                    batch_ok += 1
                    total_ok += 1

        total_done = total_ok + total_fail
        elapsed = time.time() - start_time
        rate = (total_done - len(done_urls)) / elapsed if elapsed > 0 else 0
        remaining_count = TOTAL_EXPECTED - total_done
        eta_min = remaining_count / rate / 60 if rate > 0 else 0

        log(f"  Batch done: +{batch_ok} ok, +{batch_fail} fail | "
            f"Total: {total_done}/{TOTAL_EXPECTED} | "
            f"{rate:.1f}/s | ETA {eta_min:.0f}min")

        # Save after each batch
        save_progress(results)
        write_csv(results)

        if shutdown_requested:
            log("Shutdown requested — saved progress. Run again to resume.")
            break

        time.sleep(BATCH_PAUSE)

    # Final
    save_progress(results)
    write_csv(results)
    print_summary(results)


def print_summary(results):
    ok = sum(1 for r in results if not r.get("error"))
    fail = len(results) - ok
    log("═══ COMPLETE ═══")
    log(f"  Total fetched: {len(results)}")
    log(f"  Successful:    {ok}")
    log(f"  Failed:        {fail}")
    log(f"  CSV:           {OUTPUT_CSV}")
    log(f"  Resume state:  {PROGRESS_JSON}")


if __name__ == "__main__":
    main()
