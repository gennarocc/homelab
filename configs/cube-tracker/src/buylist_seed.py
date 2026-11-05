#!/usr/bin/env python3
"""Fill the `buylist` column for the 90 days MTGJSON still serves.

track.py banks Card Kingdom's buylist from today onward, but a line needs more
than one point. MTGJSON's 90-day archive carries cardkingdom.buylist in the
same record seed.py already reads for tcgplayer.retail, so the recent window
can be filled in from it.

Only the buylist column is touched -- every existing row keeps its price, and
rows older than the archive (the MTGStocks backfill) simply stay blank, since
no free source carries buylist history that far back.

Usage:  python3 buylist_seed.py [path/to/AllPrices.json.xz]
"""

import csv
import os
import sys

import seed
import track


def main():
    archive = sys.argv[1] if len(sys.argv) > 1 else os.path.join(track.DATA_DIR,
                                                                 "AllPrices.json.xz")
    if not os.path.exists(archive):
        seed.download(archive)

    entries = track.load_cube()
    mapping = track.scryfall_to_mtgjson(entries)
    by_uuid = {}
    for e in entries.values():
        by_uuid.setdefault(mapping.get(e["scryfall_id"]), []).append(e)
    by_uuid.pop(None, None)

    # (uuid, finish, date) -> buylist price
    found = {}
    for uuid, record in seed.stream_wanted(archive, set(by_uuid)):
        buy = (((record or {}).get("paper") or {}).get("cardkingdom") or {}).get("buylist") or {}
        for e in by_uuid[uuid]:
            for day, price in (buy.get(e["finish"]) or {}).items():
                found[(uuid, e["finish"], day)] = price
    print(f"archive carries {len(found):,} buylist quotes")

    with open(track.PRICES_CSV, newline="") as f:
        rows = list(csv.DictReader(f))

    filled = 0
    for r in rows:
        if r.get("buylist"):
            continue
        p = found.get((r["uuid"], r["finish"], r["date"]))
        if p is not None:
            r["buylist"] = f"{p:.2f}"
            filled += 1

    tmp = track.PRICES_CSV + ".tmp"
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=track.FIELDS)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, track.PRICES_CSV)

    days = sorted({r["date"] for r in rows if r.get("buylist")})
    print(f"filled {filled:,} rows; buylist now covers {len(days)} days "
          f"({days[0]} -> {days[-1]})" if days else "no buylist data found")


if __name__ == "__main__":
    main()
