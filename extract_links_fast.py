#!/usr/bin/env python3
"""
FMHY Rejected Links Extractor — Fast Version
Uses regex parsing + concurrent fetching for 25k+ links.
"""

import csv
import os
import re
import sys
import time
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse
from threading import Lock

import requests
from bs4 import BeautifulSoup

# ── Config ──────────────────────────────────────────────────────────────────
HTML_FILE = "FMHY - 🗑 _ Remove - remove-sites [986617857133649921].html"
OUTPUT_CSV = "rejected_sites.csv"
PROGRESS_JSON = "progress.json"
FETCH_TIMEOUT = 12
MAX_WORKERS = 25  # concurrent fetches
MAX_LINKS = 0  # 0 = unlimited; set to e.g. 500 to test first

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


# ── Step 1: Fast HTML parse with regex ──────────────────────────────────────
def parse_html_fast(filepath):
    """Regex-based parse of DiscordChatExporter HTML — much faster than BS4."""
    print(f"\n═══ Parsing HTML export ═══")
    
    # Read the whole file
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()
    
    print(f"  File size: {len(content)/1024/1024:.1f} MB")
    
    # Extract message groups: find each message container
    # Pattern: data-message-id="XXXX" ... chatlog__author ... timestamp ... content
    msg_pattern = re.compile(
        r'data-message-id=(\d+).*?'
        r'class=chatlog__author[^>]*>([^<]+)<.*?'
        r'class=chatlog__timestamp[^>]*>([^<]+)<.*?'
        r'class="chatlog__content chatlog__markdown"[^>]*>.*?'
        r'(?:class=chatlog__markdown-preserve>)?(.*?)(?=<div class=chatlog__message-container|<div class=chatlog__message-group|$)',
        re.DOTALL
    )
    
    # Simpler: just extract all message blocks
    # Split by message-container boundaries
    blocks = re.split(r'class=chatlog__message-container data-message-id=', content)
    blocks = blocks[1:]  # skip first (before any message)
    
    messages = []
    link_pattern = re.compile(r'href="(https?://[^"]+)"')
    
    for block in blocks:
        # Get message ID
        msg_id_match = re.match(r'(\d+)', block)
        msg_id = msg_id_match.group(1) if msg_id_match else "unknown"
        
        # Get author
        author_match = re.search(r'class=chatlog__author[^>]*>([^<]+)<', block)
        author = author_match.group(1).strip() if author_match else "unknown"
        
        # Get timestamp  
        ts_match = re.search(r'class=chatlog__timestamp[^>]*>([^<]+)<', block)
        timestamp = ts_match.group(1).strip() if ts_match else "unknown"
        
        # Get links from the markdown-preserve section
        links = link_pattern.findall(block)
        
        # Get context text (first 200 chars of visible text)
        text_match = re.search(r'chatlog__content[^>]*>(.*)', block, re.DOTALL)
        context = ""
        if text_match:
            raw = text_match.group(1)[:500]
            context = re.sub(r'<[^>]+>', ' ', raw).strip()[:200]
        
        if links:
            messages.append({
                "msg_id": msg_id,
                "author": author,
                "timestamp": timestamp,
                "links": links,
                "context": context,
            })
    
    print(f"  Parsed {len(messages)} messages with links")
    return messages


# ── Step 2: Deduplicate & filter ────────────────────────────────────────────
def extract_unique_links(messages):
    """Deduplicate URLs and attach message context."""
    print(f"\n═══ Extracting unique links ═══")
    
    seen = {}
    for msg in messages:
        for url in msg["links"]:
            parsed = urlparse(url)
            
            # Skip certain domains
            if parsed.hostname and parsed.hostname in SKIP_DOMAINS:
                continue
            
            # Skip certain extensions
            path_lower = parsed.path.lower()
            if any(path_lower.endswith(ext) for ext in SKIP_EXTENSIONS):
                continue
            
            # Skip anchor-only / special schemes
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
            else:
                if msg["author"] not in seen[norm]["mentioned_by"]:
                    seen[norm]["mentioned_by"].append(msg["author"])
    
    unique = list(seen.values())
    print(f"  Found {len(unique)} unique external links")
    return unique


# ── Step 3: Concurrent fetch ────────────────────────────────────────────────
def fetch_one(url):
    """Fetch a single URL and extract title + description + body snippet."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=FETCH_TIMEOUT,
                            allow_redirects=True)
        status = resp.status_code
        final_url = resp.url
        
        if status != 200:
            return {
                "status": status,
                "final_url": final_url,
                "title": "",
                "description": "",
                "body": f"HTTP {status}",
                "error": f"HTTP {status}",
            }
        
        ct = resp.headers.get("Content-Type", "")
        if "text/html" not in ct:
            return {
                "status": status,
                "final_url": final_url,
                "title": "",
                "description": "",
                "body": f"Non-HTML: {ct[:60]}",
                "error": "",
            }
        
        soup = BeautifulSoup(resp.text, "html.parser")
        
        # Title
        title = ""
        if soup.title and soup.title.string:
            title = soup.title.string.strip()[:200]
        og = soup.find("meta", property="og:title")
        if og:
            title = og.get("content", title)[:200]
        
        # Description
        desc = ""
        md = soup.find("meta", attrs={"name": "description"})
        if md:
            desc = md.get("content", "")[:300]
        ogd = soup.find("meta", property="og:description")
        if ogd:
            desc = ogd.get("content", desc)[:300]
        
        # Body text (first 800 chars of visible text)
        for t in soup(["script", "style", "noscript"]):
            t.decompose()
        body = soup.get_text(" ", strip=True)[:800]
        
        return {
            "status": status,
            "final_url": final_url,
            "title": title,
            "description": desc,
            "body": body,
            "error": "",
        }
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


def fetch_all(links, max_workers=MAX_WORKERS):
    """Fetch all links concurrently."""
    total = len(links)
    print(f"\n═══ Fetching {total} sites ({max_workers} workers) ═══")
    
    # Load any previous progress
    done_urls = set()
    if os.path.exists(PROGRESS_JSON):
        with open(PROGRESS_JSON, "r") as f:
            prev = json.load(f)
            for r in prev:
                done_urls.add(r["url"])
        print(f"  Resuming: {len(done_urls)} already done")
    
    # Filter out already-fetched
    remaining = [l for l in links if l["url"] not in done_urls]
    results = list(prev) if os.path.exists(PROGRESS_JSON) else []
    
    print(f"  Remaining to fetch: {len(remaining)}")
    
    success = 0
    fail = 0
    start_time = time.time()
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_link = {executor.submit(fetch_one, l["url"]): l for l in remaining}
        
        for i, future in enumerate(as_completed(future_to_link)):
            link = future_to_link[future]
            try:
                result = future.result()
            except Exception as e:
                result = {"status": 0, "final_url": link["url"], "title": "",
                          "description": "", "body": "", "error": str(e)[:120]}
            
            # Merge result into link data
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
                "also_mentioned_by": ", ".join(link.get("mentioned_by", [])),
                "error": result.get("error", ""),
            }
            results.append(entry)
            
            if result.get("error"):
                fail += 1
            else:
                success += 1
            
            # Progress every 50
            done = success + fail
            if done % 50 == 0 or done == len(remaining):
                elapsed = time.time() - start_time
                rate = done / elapsed if elapsed > 0 else 0
                eta = (len(remaining) - done) / rate if rate > 0 else 0
                print(f"  [{done}/{len(remaining)}] "
                      f"{success} ok, {fail} fail | "
                      f"{rate:.1f}/s | ETA {eta/60:.1f}min")
                # Save progress atomically
                tmp = PROGRESS_JSON + ".tmp"
                with open(tmp, "w") as f:
                    json.dump(results, f)
                os.replace(tmp, PROGRESS_JSON)
    
    # Final save
    tmp = PROGRESS_JSON + ".tmp"
    with open(tmp, "w") as f:
        json.dump(results, f)
    os.replace(tmp, PROGRESS_JSON)
    
    elapsed = time.time() - start_time
    print(f"\n  Done in {elapsed/60:.1f} min | {success} ok, {fail} fail")
    return results


# ── Step 4: Write CSV ──────────────────────────────────────────────────────
def write_csv(results, filepath):
    """Write CSV."""
    print(f"\n═══ Writing CSV → {filepath} ═══")
    
    fields = ["url", "title", "description", "body", "status",
              "final_url", "author", "timestamp", "context",
              "also_mentioned_by", "error"]
    
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in results:
            row = {k: r.get(k, "") for k in fields}
            w.writerow(row)
    
    print(f"  ✅ Wrote {len(results)} rows")


# ── Main ───────────────────────────────────────────────────────────────────
def main():
    print("╔══════════════════════════════════════════════╗")
    print("║  FMHY Rejected Links — Fast Concurrent      ║")
    print("╚══════════════════════════════════════════════╝")
    
    messages = parse_html_fast(HTML_FILE)
    links = extract_unique_links(messages)
    
    if MAX_LINKS > 0:
        links = links[:MAX_LINKS]
        print(f"  ⚠ Limited to {MAX_LINKS} links for testing")
    
    results = fetch_all(links)
    write_csv(results, OUTPUT_CSV)
    
    success = sum(1 for r in results if not r.get("error"))
    fail = len(results) - success
    print(f"\n═══ Summary ═══")
    print(f"  Total links: {len(results)}")
    print(f"  Success: {success}")
    print(f"  Failed: {fail}")
    print(f"  CSV: {OUTPUT_CSV}")
    print(f"  Progress (resume): {PROGRESS_JSON}")
    print()


if __name__ == "__main__":
    main()
