#!/usr/bin/env python3
"""
Build the final deduplicated, alive-only link dataset for the website.

Reads rejected_sites.csv (master dataset from the overnight fetcher),
keeps only links that returned HTTP 200, merges duplicate URLs (keeping
the richest summary), fills in any missing summaries, and writes:
  - final_links.csv   (spreadsheet-friendly)
  - final_links.json  (site-ready)
  - dead_links.csv    (archive of everything excluded, for reference)
"""

import csv
import io
import json
import os
import re
from urllib.parse import urlparse

INPUT = "rejected_sites.csv"
OUT_CSV = "final_links.csv"
OUT_JSON = "final_links.json"
OUT_DEAD = "dead_links.csv"


def clean_text(text):
    if not text:
        return ""
    text = text.replace('\x00', '')
    text = re.sub(r'<[^>]*>', ' ', text)
    text = re.sub(r'<[^>]*$', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def generate_summary(row):
    title = clean_text(row.get("title", ""))
    desc = clean_text(row.get("description", ""))
    body = clean_text(row.get("body", ""))

    parts = []
    if title:
        parts.append(title)
    if desc and desc.lower() != title.lower():
        first_sent = re.split(r'(?<=[.!?])\s', desc)
        if first_sent:
            parts.append(first_sent[0][:200])
    if not parts and body:
        lines = [l.strip() for l in body.split('\n') if len(l.strip()) > 20]
        if lines:
            parts.append(lines[0][:200])
    if not parts:
        parsed = urlparse(row.get("url", ""))
        domain = parsed.hostname or row.get("url", "")
        parts.append(f"Site: {domain}")

    summary = " — ".join(parts)
    if len(summary) > 500:
        summary = summary[:497] + "..."
    return summary


JUNK_PATTERNS = [
    "account suspended", "loading...", "just a moment", "attention required",
    "403 forbidden", "domain for sale", "this domain is for sale",
    "parked domain", "buy this domain", "under construction", "coming soon",
]


def is_junk(row):
    text = f"{row.get('title', '')} {row.get('summary', '')}".strip().lower()
    return any(p in text for p in JUNK_PATTERNS)


def norm_url(u):
    u = (u or "").strip().lower()
    if u.endswith('/'):
        u = u[:-1]
    return u


def score(row):
    return (1 if row.get("summary", "").strip() else 0, len(row.get("summary", "") or ""))


def main():
    with open(INPUT, "r", encoding="utf-8", errors="replace") as f:
        content = f.read().replace('\x00', '')
    rows = list(csv.DictReader(io.StringIO(content)))
    print(f"Read {len(rows)} rows from {INPUT}")

    alive = [r for r in rows if r.get("status", "").strip() == "200"]
    dead = [r for r in rows if r.get("status", "").strip() != "200"]
    print(f"Alive (200): {len(alive)}  |  Dead/other: {len(dead)}")

    by_url = {}
    for row in alive:
        summary = row.get("summary", "").strip()
        if not summary:
            summary = generate_summary(row)
        row["summary"] = summary

        key = norm_url(row["url"])
        if key not in by_url or score(row) > score(by_url[key]):
            by_url[key] = row

    deduped = list(by_url.values())
    final_rows = sorted(
        (r for r in deduped if not is_junk(r)),
        key=lambda r: r["url"].lower(),
    )
    junk_removed = len(deduped) - len(final_rows)
    print(f"Final deduplicated alive links: {len(final_rows)} (removed {junk_removed} junk entries)")

    fields = ["url", "title", "summary", "final_url", "author", "also_mentioned_by"]
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in final_rows:
            w.writerow({k: clean_text(r.get(k, "")) if k != "url" else r.get(k, "") for k in fields})
    print(f"Wrote {OUT_CSV}")

    json_data = [
        {
            "url": r["url"],
            "title": clean_text(r.get("title", "")) or None,
            "summary": r["summary"],
        }
        for r in final_rows
    ]
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(json_data, f, indent=2, ensure_ascii=False)
    print(f"Wrote {OUT_JSON}")

    with open(OUT_DEAD, "w", newline="", encoding="utf-8") as f:
        fields_dead = ["url", "status", "error", "author"]
        w = csv.DictWriter(f, fieldnames=fields_dead)
        w.writeheader()
        for r in dead:
            w.writerow({k: r.get(k, "") for k in fields_dead})
    print(f"Wrote {OUT_DEAD} ({len(dead)} rows, archived for reference)")


if __name__ == "__main__":
    main()
