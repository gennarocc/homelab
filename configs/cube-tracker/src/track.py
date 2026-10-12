#!/usr/bin/env python3
"""Append today's TCGplayer prices for every card in the cube to prices.csv.

MTGJSON only serves 90 days of price history, so the only way to ever answer
"what moved over the last year" is to bank a snapshot every day. One row per
unique printing per day; duplicates in the cube are carried in the `copies`
column rather than as repeated rows.

Stdlib only -- this runs on a Raspberry Pi in a python:3.12-alpine container
with no pip install step.
"""

import csv
import gzip
import json
import os
import sys
import time
import urllib.request
from datetime import date, timezone, datetime

CUBE_ID = os.environ.get("CUBE_ID", "alpha2onslaught")
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
SETS_DIR = os.path.join(DATA_DIR, "sets")
PRICES_CSV = os.path.join(DATA_DIR, "prices.csv")

CUBE_URL = f"https://cubecobra.com/cube/api/cubeJSON/{CUBE_ID}"
CUBE_NAME = CUBE_ID   # replaced with the cube's real name on the first fetch
SET_DATES = {}        # set code -> release date, for the condition haircuts
TODAY_URL = "https://mtgjson.com/api/v5/AllPricesToday.json.gz"
SET_URL = "https://mtgjson.com/api/v5/{code}.json"

FIELDS = ["date", "uuid", "name", "set", "number", "finish", "copies", "price"]


def download(url, attempts=6):
    """Fetch a URL into memory, resuming with Range requests until complete.

    A plain read() of the MTGJSON bundles comes back short often enough that a
    daily cron job would miss days; the server honors Range, so pick up where
    the stream died instead of starting over.
    """
    body = bytearray()
    expected = None
    for attempt in range(1, attempts + 1):
        headers = {"User-Agent": "cube-tracker/1.0", "Accept-Encoding": "identity"}
        if body:
            headers["Range"] = f"bytes={len(body)}-"
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, headers=headers), timeout=180
            ) as r:
                if expected is None:
                    expected = int(r.headers.get("Content-Length") or 0) or None
                if r.status == 200 and body:
                    body = bytearray()  # server ignored the Range; start clean
                while True:
                    chunk = r.read(1 << 16)
                    if not chunk:
                        break
                    body += chunk
        except Exception as exc:
            print(f"warn: {url} attempt {attempt}: {exc}", file=sys.stderr)
        if expected and len(body) >= expected:
            return bytes(body)
        if expected is None:
            return bytes(body)  # chunked response; fetch() validates by parsing
        if attempt < attempts:
            time.sleep(3 * attempt)
    raise RuntimeError(f"{url}: got {len(body)} of {expected} bytes after {attempts} attempts")


def fetch(url, gz=False, attempts=4):
    """A truncated chunked response looks like a successful read, so the only
    reliable completeness check is whether the payload still parses."""
    for attempt in range(1, attempts + 1):
        raw = download(url)
        try:
            return json.loads(gzip.decompress(raw) if gz else raw)
        except Exception as exc:
            if attempt == attempts:
                raise
            print(f"warn: {url} incomplete ({exc}); retrying", file=sys.stderr)
            time.sleep(3 * attempt)


def load_cube():
    """Return {(scryfall_id, finish): {name, set, number, finish, copies}}.

    Keyed by printing *and* finish because the same card can appear twice in
    the cube in different finishes, and those are different prices.
    """
    global CUBE_NAME
    cube = fetch(CUBE_URL)
    CUBE_NAME = cube.get("name") or CUBE_ID
    entries = {}
    for card in cube["cards"]["mainboard"]:
        det = card.get("details") or {}
        sid = det.get("scryfall_id") or card.get("cardID")
        finish = "foil" if card.get("finish") == "Foil" else "normal"
        key = (sid, finish)
        if key in entries:
            entries[key]["copies"] += 1
            continue
        entries[key] = {
            "scryfall_id": sid,
            "name": card.get("name") or det.get("name"),
            "set": (det.get("set") or "").upper(),
            "number": det.get("collector_number") or "",
            "finish": finish,
            "copies": 1,
        }
    return entries


def set_file(code):
    """Cache MTGJSON set files on disk; they change only when a set is errata'd."""
    path = os.path.join(SETS_DIR, f"{code}.json")
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        os.makedirs(SETS_DIR, exist_ok=True)
        data = fetch(SET_URL.format(code=code))
        with open(path, "w") as f:
            json.dump(data, f)
    with open(path) as f:
        return json.load(f)


def scryfall_to_mtgjson(entries):
    """MTGJSON prices are keyed by its own uuid, so bridge via the set files."""
    wanted = {e["scryfall_id"] for e in entries.values()}
    mapping = {}
    for code in sorted({e["set"] for e in entries.values()}):
        try:
            data = set_file(code)["data"]
            SET_DATES[code] = data.get("releaseDate") or ""
        except Exception as exc:  # a missing set should not kill the whole run
            print(f"warn: set {code} unavailable: {exc}", file=sys.stderr)
            continue
        for card in data.get("cards", []) + data.get("tokens", []):
            sid = (card.get("identifiers") or {}).get("scryfallId")
            if sid in wanted:
                mapping[sid] = card["uuid"]
    return mapping


def tcg_price(record, finish):
    retail = (((record or {}).get("paper") or {}).get("tcgplayer") or {}).get("retail") or {}
    series = retail.get(finish) or {}
    if not series:
        return None, None
    # One date per card in AllPricesToday, but take the latest defensively.
    day = max(series)
    return day, series[day]


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    entries = load_cube()
    mapping = scryfall_to_mtgjson(entries)
    prices = fetch(TODAY_URL, gz=True)
    as_of = prices["meta"]["date"]
    data = prices["data"]

    rows, missing = [], []
    for entry in entries.values():
        uuid = mapping.get(entry["scryfall_id"])
        day, price = tcg_price(data.get(uuid), entry["finish"]) if uuid else (None, None)
        if price is None:
            missing.append(f"{entry['name']} [{entry['set']}-{entry['number']} {entry['finish']}]")
            continue
        rows.append({
            "date": day or as_of,
            "uuid": uuid,
            "name": entry["name"],
            "set": entry["set"],
            "number": entry["number"],
            "finish": entry["finish"],
            "copies": entry["copies"],
            "price": f"{price:.2f}",
        })

    if not rows:
        sys.exit("no prices resolved -- refusing to write an empty snapshot")

    # Drop the whole-of-Magic price dict before reading the CSV back in. It is
    # ~500 MB and nothing below needs it; holding both at once is what would
    # push a multi-year history over the container's memory limit.
    del prices, data

    # Re-running the same day replaces that day rather than double-counting it.
    existing = []
    if os.path.exists(PRICES_CSV):
        with open(PRICES_CSV, newline="") as f:
            existing = [r for r in csv.DictReader(f) if r["date"] != rows[0]["date"]]

    tmp = PRICES_CSV + ".tmp"
    with open(tmp, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(existing)
        writer.writerows(rows)
    os.replace(tmp, PRICES_CSV)   # the banked history is the one unrecoverable
                                  # file here; never leave it half-written

    total = sum(float(r["price"]) * int(r["copies"]) for r in rows)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"{stamp} {as_of}: {len(rows)} printings, ${total:,.2f}, {len(missing)} unpriced")
    for m in missing:
        print(f"  unpriced: {m}")

    # The page titles itself from the cube's own name, so renaming it on
    # CubeCobra renames the dashboard on the next run.
    with open(os.path.join(DATA_DIR, "cube.json"), "w") as f:
        json.dump({"id": CUBE_ID, "name": CUBE_NAME, "sets": SET_DATES}, f)

    # Art is cosmetic: a Scryfall hiccup must never fail a price snapshot,
    # and every day missed on prices is gone permanently.
    try:
        import images
        added, failed = images.sync(entries, os.path.join(DATA_DIR, "images"))
        if added or failed:
            print(f"images: {added} added, {failed} failed")
    except Exception as exc:
        print(f"warn: image sync skipped: {exc}", file=sys.stderr)

    import render
    render.build(PRICES_CSV, os.path.join(DATA_DIR, "dashboard.html"))


if __name__ == "__main__":
    main()
