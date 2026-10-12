#!/usr/bin/env python3
"""One-time backfill: seed prices.csv with MTGJSON's 90 days of history.

Run once, before the daily job takes over. AllPrices.json.xz is ~48 MB and
decompresses to a few GB, so it is scanned as a stream and only the cube's
uuids are ever materialized.

Usage: python3 seed.py [path/to/AllPrices.json.xz]   (downloads if omitted)
"""

import csv
import json
import lzma
import os
import sys
import urllib.request

import track

ARCHIVE_URL = "https://mtgjson.com/api/v5/AllPrices.json.xz"
CHUNK = 1 << 20


def download(path):
    print(f"downloading {ARCHIVE_URL} ...")
    req = urllib.request.Request(ARCHIVE_URL, headers={"User-Agent": "cube-tracker/1.0"})
    with urllib.request.urlopen(req, timeout=600) as r, open(path, "wb") as f:
        while True:
            chunk = r.read(CHUNK)
            if not chunk:
                break
            f.write(chunk)
    print(f"  {os.path.getsize(path) / 1e6:.0f} MB")


def stream_wanted(archive, wanted):
    """Yield (uuid, record) for uuids in `wanted`, without loading the whole file.

    The archive is one object: {"meta":{...},"data":{"<uuid>":{...}, ...}}. We
    walk the data object key by key and only json.loads the values we want.
    """
    f = lzma.open(archive, "rt", encoding="utf-8")
    buf = f.read(CHUNK)
    i = buf.index('"data":') + len('"data":')
    while buf[i] in " \n\t":
        i += 1
    i += 1  # step past the opening brace of the data object

    while True:
        while True:
            while i < len(buf) and buf[i] in " \n\t,":
                i += 1
            if i < len(buf):
                break
            chunk = f.read(CHUNK)
            if not chunk:
                return
            buf = buf[i:] + chunk
            i = 0
        if buf[i] == "}":
            return

        while buf.find('"', i + 1) == -1:
            chunk = f.read(CHUNK)
            if not chunk:
                return
            buf += chunk
        end_key = buf.index('"', i + 1)
        uuid = buf[i + 1:end_key]
        start = buf.index(":", end_key) + 1
        while start < len(buf) and buf[start] in " \n\t":
            start += 1

        depth, p, in_string, escaped = 0, start, False, False
        while True:
            if p >= len(buf):
                chunk = f.read(CHUNK)
                if not chunk:
                    raise SystemExit("archive ended mid-record")
                buf += chunk
                continue
            ch = buf[p]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
            elif ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    p += 1
                    break
            p += 1

        if uuid in wanted:
            yield uuid, json.loads(buf[start:p])
        i = p
        if i > CHUNK:
            buf = buf[i:]
            i = 0


def main():
    archive = sys.argv[1] if len(sys.argv) > 1 else os.path.join(track.DATA_DIR, "AllPrices.json.xz")
    os.makedirs(track.DATA_DIR, exist_ok=True)
    if not os.path.exists(archive):
        download(archive)

    entries = track.load_cube()
    mapping = track.scryfall_to_mtgjson(entries)
    by_uuid = {}
    for entry in entries.values():
        by_uuid.setdefault(mapping.get(entry["scryfall_id"]), []).append(entry)
    by_uuid.pop(None, None)

    rows = []
    for uuid, record in stream_wanted(archive, set(by_uuid)):
        retail = (((record or {}).get("paper") or {}).get("tcgplayer") or {}).get("retail") or {}
        for entry in by_uuid[uuid]:
            for day, price in sorted((retail.get(entry["finish"]) or {}).items()):
                rows.append({
                    "date": day, "uuid": uuid, "name": entry["name"], "set": entry["set"],
                    "number": entry["number"], "finish": entry["finish"],
                    "copies": entry["copies"], "price": f"{price:.2f}",
                })

    rows.sort(key=lambda r: (r["date"], r["name"]))
    with open(track.PRICES_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=track.FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    days = sorted({r["date"] for r in rows})
    print(f"seeded {len(rows):,} rows, {len(days)} days: {days[0]} -> {days[-1]}")


if __name__ == "__main__":
    main()
