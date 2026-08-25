#!/usr/bin/env python3
"""
FMHY Rejected Links Extractor
Parses Discord HTML export, extracts all website links, fetches each one,
and outputs a CSV with details + summaries.
"""

import csv
import json
import os
import re
import sys
import time
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

# ── Config ──────────────────────────────────────────────────────────────────
HTML_FILE = "FMHY - 🗑 _ Remove - remove-sites [986617857133649921].html"
OUTPUT_CSV = "rejected_sites.csv"
FETCH_TIMEOUT = 15
DELAY_BETWEEN_FETCHES = 1.5  # seconds between fetches to be polite
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/125.0.0.0 Safari/537.36"
}

# Domains to skip (discord internal, anchors, CDNs, etc.)
SKIP_DOMAINS = {
    "cdn.discordapp.com", "media.discordapp.net",
    "cdn.discord.com", "images-ext-1.discordapp.net",
    "discord.com", "discord.gg", "discordapp.com",
    "github.com",  # skip github links (not what user wants to summarize)
    "i.imgur.com",  # skip direct image links
}
SKIP_PREFIXES = ["#chatlog__", "javascript:", "mailto:", "data:"]

# ── Step 1: Try Discord API ────────────────────────────────────────────────
def try_discord_api():
    """Attempt to fetch messages via Discord API. Will fail without a token."""
    print("\n═══ Step 1: Trying Discord API ═══")
    SERVER_ID = "956006107564879872"
    CHANNEL_ID = "986617857133649921"
    url = f"https://discord.com/api/v9/channels/{CHANNEL_ID}/messages?limit=100"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            msgs = resp.json()
            print(f"  ✅ Fetched {len(msgs)} messages via API!")
            return msgs
        elif resp.status_code == 401 or resp.status_code == 403:
            print(f"  ❌ Auth required (status {resp.status_code}). "
                  f"No Discord token provided — will use HTML export.")
        else:
            print(f"  ❌ API returned status {resp.status_code}: "
                  f"{resp.text[:200]}")
    except Exception as e:
        print(f"  ❌ API request failed: {e}")

    print("  ℹ️  Proceeding with HTML export only.")
    return None


# ── Step 2: Parse HTML Export ──────────────────────────────────────────────
def parse_html_export(filepath):
    """Parse the DiscordChatExporter HTML and extract messages + links."""
    print(f"\n═══ Step 2: Parsing HTML export ({filepath}) ═══")

    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        soup = BeautifulSoup(f, "html.parser")

    messages = []
    groups = soup.find_all("div", class_="chatlog__message-group")
    print(f"  Found {len(groups)} message groups")

    for group in groups:
        containers = group.find_all("div", class_="chatlog__message-container")
        for container in containers:
            msg_id = container.get("data-message-id", "unknown")

            # Author
            author_el = container.find("span", class_="chatlog__author")
            author = author_el.get_text(strip=True) if author_el else "unknown"

            # Timestamp
            ts_el = container.find("span", class_="chatlog__timestamp")
            timestamp = ts_el.get_text(strip=True) if ts_el else "unknown"

            # Content — get all links
            content_div = container.find("div", class_="chatlog__content")
            if not content_div:
                continue

            # Full text of the message (for context)
            msg_text = content_div.get_text(separator=" ", strip=True)

            # Extract all <a> links
            links = []
            for a_tag in content_div.find_all("a", href=True):
                href = a_tag["href"].strip()
                link_text = a_tag.get_text(strip=True)
                links.append({"url": href, "text": link_text})

            messages.append({
                "msg_id": msg_id,
                "author": author,
                "timestamp": timestamp,
                "text": msg_text,
                "links": links,
            })

    print(f"  Parsed {len(messages)} messages")
    return messages


# ── Step 3: Deduplicate & Filter Links ─────────────────────────────────────
def extract_unique_links(messages):
    """Build a deduplicated list of external URLs with their context."""
    print("\n═══ Step 3: Extracting unique links ═══")

    seen = {}
    for msg in messages:
        for link in msg["links"]:
            url = link["url"]

            # Skip non-http links
            if not url.startswith("http"):
                continue

            # Skip certain domains
            parsed = urlparse(url)
            if parsed.hostname in SKIP_DOMAINS:
                continue

            # Skip anchor-only links
            if any(url.startswith(p) for p in SKIP_PREFIXES):
                continue

            # Skip image extensions
            if any(url.lower().endswith(ext) for ext in
                   [".png", ".jpg", ".jpeg", ".gif", ".gifv", ".webp",
                    ".svg", ".ico", ".woff2", ".woff", ".ttf"]):
                continue

            # Normalize URL
            norm_url = url.rstrip("/")

            if norm_url not in seen:
                seen[norm_url] = {
                    "url": norm_url,
                    "original_url": url,
                    "link_text": link["text"],
                    "author": msg["author"],
                    "timestamp": msg["timestamp"],
                    "context": msg["text"][:300],
                }
            else:
                # Append additional context if mentioned more than once
                existing = seen[norm_url]
                if msg["author"] not in existing.get("also_mentioned_by", ""):
                    existing["also_mentioned_by"] = (
                        existing.get("also_mentioned_by", "") +
                        f", {msg['author']}"
                    )

    unique = list(seen.values())
    print(f"  Found {len(unique)} unique external links (after filtering)")
    return unique


# ── Step 4: Fetch & Parse Each Site ────────────────────────────────────────
def fetch_and_parse(url, timeout=FETCH_TIMEOUT):
    """Fetch a URL and extract title, description, and key text."""
    try:
        resp = requests.get(
            url,
            headers=HEADERS,
            timeout=timeout,
            allow_redirects=True,
            verify=True,
        )
        status_code = resp.status_code
        final_url = resp.url

        if status_code != 200:
            return {
                "status_code": status_code,
                "final_url": final_url,
                "title": "",
                "description": "",
                "body_text": f"HTTP {status_code}",
                "error": f"HTTP {status_code}",
            }

        # Check if it's HTML
        content_type = resp.headers.get("Content-Type", "")
        if "text/html" not in content_type:
            return {
                "status_code": status_code,
                "final_url": final_url,
                "title": "",
                "description": "",
                "body_text": f"Non-HTML content: {content_type}",
                "error": "",
            }

        soup = BeautifulSoup(resp.text, "html.parser")

        # Title
        title = ""
        if soup.title and soup.title.string:
            title = soup.title.string.strip()
        og_title = soup.find("meta", property="og:title")
        if og_title:
            title = og_title.get("content", title)

        # Description
        description = ""
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            description = meta_desc.get("content", "")
        og_desc = soup.find("meta", property="og:description")
        if og_desc:
            description = og_desc.get("content", description)

        # Body text — get visible text, strip scripts/styles
        for tag in soup(["script", "style", "nav", "footer", "header",
                         "noscript", "iframe"]):
            tag.decompose()

        body_text = soup.get_text(separator=" ", strip=True)
        # Truncate to first 1000 chars
        body_text = body_text[:1000]

        return {
            "status_code": status_code,
            "final_url": final_url,
            "title": title,
            "description": description,
            "body_text": body_text,
            "error": "",
        }

    except requests.exceptions.Timeout:
        return {
            "status_code": 0, "final_url": url, "title": "",
            "description": "", "body_text": "",
            "error": "Timeout",
        }
    except requests.exceptions.SSLError:
        return {
            "status_code": 0, "final_url": url, "title": "",
            "description": "", "body_text": "",
            "error": "SSL Error",
        }
    except requests.exceptions.ConnectionError as e:
        return {
            "status_code": 0, "final_url": url, "title": "",
            "description": "", "body_text": "",
            "error": f"Connection Error: {str(e)[:100]}",
        }
    except Exception as e:
        return {
            "status_code": 0, "final_url": url, "title": "",
            "description": "", "body_text": "",
            "error": str(e)[:200],
        }


def fetch_all_sites(links):
    """Fetch all sites and enrich the link data."""
    print(f"\n═══ Step 4: Fetching {len(links)} sites ═══")
    total = len(links)

    for i, link in enumerate(links):
        url = link["url"]
        short_url = url[:70] + "..." if len(url) > 70 else url
        print(f"  [{i+1}/{total}] {short_url}", end="", flush=True)

        result = fetch_and_parse(url)
        link["status_code"] = result["status_code"]
        link["final_url"] = result["final_url"]
        link["title"] = result["title"]
        link["description"] = result["description"]
        link["body_text"] = result["body_text"]
        link["fetch_error"] = result["error"]

        if result["error"]:
            print(f" ⚠ {result['error'][:40]}")
        else:
            print(f" ✅ {result['status_code']}")

        if i < total - 1:
            time.sleep(DELAY_BETWEEN_FETCHES)

    return links


# ── Step 5: Write CSV ──────────────────────────────────────────────────────
def write_csv(links, filepath):
    """Write the final CSV with all data."""
    print(f"\n═══ Step 5: Writing CSV → {filepath} ═══")

    fieldnames = [
        "url", "title", "description", "body_text", "status_code",
        "final_url", "link_text", "author", "timestamp", "context",
        "also_mentioned_by", "fetch_error",
    ]

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for link in links:
            row = {k: link.get(k, "") for k in fieldnames}
            writer.writerow(row)

    print(f"  ✅ Wrote {len(links)} rows to {filepath}")


# ── Main ───────────────────────────────────────────────────────────────────
def main():
    print("╔══════════════════════════════════════════╗")
    print("║  FMHY Rejected Links Extractor          ║")
    print("╚══════════════════════════════════════════╝")

    # Step 1: Try Discord API
    api_messages = try_discord_api()

    # Step 2: Parse HTML export
    messages = parse_html_export(HTML_FILE)

    # If API had extra messages, merge them (skip for now — HTML is primary)
    if api_messages:
        print(f"  ℹ️  API returned {len(api_messages)} messages (not merged yet)")

    # Step 3: Extract unique links
    links = extract_unique_links(messages)

    # Step 4: Fetch each site
    links = fetch_all_sites(links)

    # Step 5: Write CSV
    write_csv(links, OUTPUT_CSV)

    # Summary
    success = sum(1 for l in links if not l.get("fetch_error"))
    failed = len(links) - success
    print(f"\n═══ Summary ═══")
    print(f"  Total unique links: {len(links)}")
    print(f"  Successfully fetched: {success}")
    print(f"  Failed: {failed}")
    print(f"  Output: {OUTPUT_CSV}")
    print()


if __name__ == "__main__":
    main()
