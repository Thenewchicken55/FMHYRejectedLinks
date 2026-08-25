#!/usr/bin/env python3
"""
Enhance rejected_sites.csv with better summaries.

Reads the CSV produced by the overnight fetcher, cleans up all fields,
and generates a richer summary column from title + description + body.

Usage:
    python3 generate_summaries.py                    # default
    python3 generate_summaries.py --input my.csv     # custom input
"""

import argparse
import csv
import os
import re
import sys


def clean_text(text):
    """Strip HTML, NUL chars, normalize whitespace."""
    if not text:
        return ""
    text = text.replace('\x00', '')
    text = re.sub(r'<[^>]*>', ' ', text)   # complete tags
    text = re.sub(r'<[^>]*$', ' ', text)    # truncated tags at end
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def generate_summary(row):
    """Generate a meaningful one-line summary from available data."""
    url = row.get("url", "")
    title = clean_text(row.get("title", ""))
    desc = clean_text(row.get("description", ""))
    body = clean_text(row.get("body", ""))
    error = row.get("error", "")

    if error and not title and not desc:
        return f"[{error}] {url}"

    parts = []

    # Title is the strongest signal
    if title:
        parts.append(title)

    # Description adds context (skip if it's the same as title)
    if desc and desc.lower() != title.lower():
        # Take first sentence of description
        first_sent = re.split(r'(?<=[.!?])\s', desc)
        if first_sent:
            parts.append(first_sent[0][:200])

    # If we still have nothing, use first meaningful line of body
    if not parts and body:
        # Skip navigation-like text
        lines = [l.strip() for l in body.split('\n') if len(l.strip()) > 20]
        if lines:
            parts.append(lines[0][:200])

    # If still nothing, extract domain purpose from URL
    if not parts:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        domain = parsed.hostname or url
        parts.append(f"Site: {domain}")

    summary = " — ".join(parts)

    # Truncate if too long
    if len(summary) > 500:
        summary = summary[:497] + "..."

    return summary


def main():
    parser = argparse.ArgumentParser(description="Enhance CSV with summaries")
    parser.add_argument("--input", default="rejected_sites.csv",
                        help="Input CSV file")
    parser.add_argument("--output", default=None,
                        help="Output CSV (default: overwrites input)")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"ERROR: {args.input} not found")
        sys.exit(1)

    output = args.output or args.input

    # Read
    print(f"Reading {args.input}...")
    with open(args.input, 'r', encoding='utf-8', errors='replace') as f:
        content = f.read()
    content = content.replace('\x00', '')

    import io
    reader = csv.DictReader(io.StringIO(content))
    rows = list(reader)
    print(f"  {len(rows)} rows")

    # Clean + summarize
    fields = list(rows[0].keys()) if rows else []
    if "summary" not in fields:
        fields.append("summary")

    print("Cleaning and generating summaries...")
    for row in rows:
        # Clean all text fields
        for key in ["title", "description", "body", "context"]:
            if key in row:
                row[key] = clean_text(row[key])

        # Generate summary
        row["summary"] = generate_summary(row)

    # Stats
    with_summary = sum(1 for r in rows if r.get("summary"))
    with_title = sum(1 for r in rows if r.get("title"))
    with_desc = sum(1 for r in rows if r.get("description"))
    errors = sum(1 for r in rows if r.get("error"))

    print(f"  With title:      {with_title}")
    print(f"  With description: {with_desc}")
    print(f"  With summary:     {with_summary}")
    print(f"  Errors:           {errors}")

    # Write
    print(f"Writing {output}...")
    tmp = output + ".tmp"
    with open(tmp, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fields})
    os.replace(tmp, output)

    print(f"✅ Done! {len(rows)} rows written to {output}")


if __name__ == "__main__":
    main()
