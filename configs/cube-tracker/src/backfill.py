#!/usr/bin/env python3
"""Extend prices.csv backwards using MTGStocks' daily TCGplayer market series.

MTGJSON only serves 90 days, which is why this project banks a snapshot daily.
MTGStocks keeps a `market` series per printing going back to 2017-11-24, and it
is the *same* figure -- spot-checked to the cent against what we already bank:

    Gaea's Cradle USG   ours $1,635.37   mtgstocks market $1,635.37
    Scrubland 3ED       ours   $452.96   mtgstocks market   $452.96

so the two splice without a discontinuity. Only dates strictly older than the
CSV's current earliest day are written; the banked MTGJSON window is never
touched.

Two things to be honest about:
  * This is an undocumented internal API with no published terms. One polite,
    cached, rate-limited pass -- not something to put on a schedule.
  * Historical rows carry *today's* cube list and copy counts, because what the
    cube held in 2024 is not recorded anywhere. seed.py already did this for the
    90-day window, so the whole series shares the assumption.

Usage:  python3 backfill.py [years]      (default 2)

Stdlib only.
"""

import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

import track

API = "https://api.mtgstocks.com"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0 Safari/537.36")
DELAY = 1.0                      # MTGStocks starts 429ing around 3 req/s
COOLOFF = 60                     # what a 429 actually wants: back right off
MIN_OVERLAP = 20                 # days of banked data needed to trust a match
MIN_VALUE_COVER = 0.99           # refuse to write a history missing real value
MAX_MEDIAN_DRIFT = 0.02          # 2%: timing noise yes, wrong printing no
CACHE = os.path.join(track.DATA_DIR, "mtgstocks")


def get(path, attempts=5):
    for attempt in range(1, attempts + 1):
        try:
            req = urllib.request.Request(f"{API}/{path}", headers={
                "User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=45) as r:
                out = json.load(r)
            time.sleep(DELAY)
            return out
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == attempts:
                raise
            # A 429 answered with another request a second later is just a
            # second 429. Stop knocking for a while.
            print(f"warn: rate limited; sleeping {COOLOFF}s", file=sys.stderr, flush=True)
            time.sleep(COOLOFF)
        except Exception as exc:
            if attempt == attempts:
                raise
            print(f"warn: {path}: {exc}; retrying", file=sys.stderr)
            time.sleep(2 * attempt + DELAY)


def norm(n):
    """Collector numbers differ in padding and decoration between the two."""
    return re.sub(r"^0+", "", str(n or "")).strip().lower()


def bare(n):
    """Drop a star or trailing letter: '7ED-300*' and 'ATQ-80d' are variants
    MTGStocks files under the plain number."""
    return re.sub(r"[^0-9]", "", norm(n))


def build_map(entries, log=print):
    """(set, number, finish) -> (print id, which series to read)."""
    sets = {s["abbreviation"]: s for s in get("card_sets")}
    wanted = {}
    for e in entries.values():
        wanted.setdefault(e["set"], []).append(e)

    mapping, missing = {}, []
    for code, group in sorted(wanted.items()):
        st = sets.get(code)
        if not st:
            missing += [f'{code}-{e["number"]} {e["name"]}' for e in group]
            continue
        try:
            prints = get(f"card_sets/{st['id']}")["prints"]
        except Exception as exc:
            log(f"warn: set {code}: {exc}")
            missing += [f'{code}-{e["number"]} {e["name"]}' for e in group]
            continue

        by_num, by_bare, by_name = {}, {}, {}
        for p in prints:
            by_num.setdefault(norm(p.get("collector_number")), []).append(p)
            by_bare.setdefault(bare(p.get("collector_number")), []).append(p)
            by_name.setdefault((p.get("name") or "").lower(), []).append(p)

        for e in group:
            cands = (by_num.get(norm(e["number"])) or by_bare.get(bare(e["number"]))
                     or by_name.get(e["name"].lower()))
            if not cands:
                missing.append(f'{code}-{e["number"]} {e["name"]}')
                continue
            want_foil = e["finish"] == "foil"
            key = "market_foil" if want_foil else "market"
            # Keep every candidate. Antiquities files all four Mishra's Factory
            # artworks under collector number 80, separated only by a season in
            # the name; the banked overlap decides which is which, below.
            ordered = ([p for p in cands if bool(p.get("foil")) == want_foil] +
                       [p for p in cands if bool(p.get("foil")) != want_foil])
            mapping[(e["set"], e["number"], e["finish"])] = [(p["id"], key) for p in ordered]
    return mapping, missing


def series_for(print_id, key):
    """Cached {date: price}. Cached so a failed run resumes for free."""
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, f"{print_id}.json")
    if os.path.exists(path) and os.path.getsize(path) > 0:
        with open(path) as f:
            raw = json.load(f)
    else:
        raw = get(f"prints/{print_id}/prices")
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({k: v for k, v in raw.items() if k in ("market", "market_foil")},
                      f, separators=(",", ":"))
        os.replace(tmp, path)
    out = {}
    for stamp, price in raw.get(key) or []:
        if price is None:
            continue
        d = datetime.fromtimestamp(stamp / 1000, timezone.utc).date().isoformat()
        out[d] = float(price)
    return out


def drift(ser, known):
    """Median relative gap over the days both sources cover, or None."""
    common = sorted(set(ser) & set(known))
    if len(common) < MIN_OVERLAP:
        return None, len(common)
    rel = sorted(abs(ser[d] - known[d]) / known[d] for d in common if known[d] > 0)
    return (rel[len(rel) // 2] if rel else None), len(common)


def agrees(ser, known, label, log=print):
    """Accept a mapping only if it reproduces the 90 days we already banked.

    The fallbacks that lift coverage from 93% to 99% -- strip the star, strip
    the variant letter, match on name -- are exactly the ones that can land on
    the wrong printing. Mishra's Factory ATQ has four artworks (80a-80d) and
    the stripped number matches all of them. The overlap is free ground truth,
    so every match has to earn its place against it.
    """
    common = sorted(set(ser) & set(known))
    if len(common) < MIN_OVERLAP:
        log(f"  reject {label}: only {len(common)} overlapping days")
        return False
    rel = sorted(abs(ser[d] - known[d]) / known[d] for d in common if known[d] > 0)
    med = rel[len(rel) // 2]
    if med > MAX_MEDIAN_DRIFT:
        log(f"  reject {label}: median {med * 100:.0f}% off over {len(common)} days")
        return False
    return True


def main():
    years = float(sys.argv[1]) if len(sys.argv) > 1 else 2.0
    entries = track.load_cube()
    uuids = track.scryfall_to_mtgjson(entries)

    with open(track.PRICES_CSV, newline="") as f:
        existing = list(csv.DictReader(f))
    if not existing:
        sys.exit("prices.csv is empty -- run seed.py first")
    earliest = min(r["date"] for r in existing)
    start = (date.fromisoformat(earliest) - timedelta(days=round(years * 365.25))).isoformat()
    print(f"banked history starts {earliest}; filling {start} .. {earliest} (exclusive)")

    mapping, missing = build_map(entries)
    print(f"matched {len(mapping)}/{len(entries)} printings on MTGStocks")
    for m in missing:
        print(f"  unmatched: {m}")

    # what we already banked, to check each match against
    known = {}
    for r in existing:
        known.setdefault((r["set"], r["number"], r["finish"]), {})[r["date"]] = float(r["price"])

    added, rows, unmatched, rejects = 0, [], [], []
    for i, (e_key, e) in enumerate(entries.items(), 1):
        uuid = uuids.get(e["scryfall_id"])
        ckey = (e["set"], e["number"], e["finish"])
        cands = mapping.get(ckey)
        label = f'{e["set"]}-{e["number"]} {e["finish"]} {e["name"]}'
        if not uuid or not cands:
            unmatched.append(label)
            continue
        # Score every candidate against the banked overlap and keep the closest
        # one that is actually close. One candidate is the normal case.
        best, best_drift, overlap = None, None, 0
        for cand in cands:
            try:
                ser = series_for(*cand)
            except Exception as exc:
                print(f"warn: {label}: {exc}", file=sys.stderr, flush=True)
                continue
            d, n = drift(ser, known.get(ckey, {}))
            overlap = max(overlap, n)
            if d is not None and (best_drift is None or d < best_drift):
                best, best_drift = ser, d
        if best is None or best_drift > MAX_MEDIAN_DRIFT:
            reason = (f"only {overlap} overlapping days" if best_drift is None
                      else f"closest of {len(cands)} candidates still {best_drift*100:.0f}% off")
            print(f"  reject {label}: {reason}")
            rejects.append(label)
            continue
        ser = best
        for d, price in ser.items():
            if start <= d < earliest:
                rows.append({"date": d, "uuid": uuid, "name": e["name"], "set": e["set"],
                             "number": e["number"], "finish": e["finish"],
                             "copies": e["copies"], "price": f"{price:.2f}"})
        added += 1
        if i % 50 == 0:
            print(f"  {i}/{len(entries)} printings, {len(rows):,} rows so far", flush=True)

    if not rows:
        sys.exit("nothing older than the existing window -- refusing to rewrite")

    # A partial backfill is worse than none: the missing printings make the
    # cube look cheaper in the past, which shows up as a cliff on the splice
    # date that reads as a real market move. Measure the gap in value, not in
    # card count, and refuse rather than ship a misleading chart.
    latest = max(r["date"] for r in existing)
    today_val, covered_val = 0.0, 0.0
    done = {(r["set"], r["number"], r["finish"]) for r in rows}
    for r in existing:
        if r["date"] != latest:
            continue
        v = float(r["price"]) * int(r["copies"])
        today_val += v
        if (r["set"], r["number"], r["finish"]) in done:
            covered_val += v
    cover = covered_val / today_val if today_val else 0
    print(f"\nbackfill covers ${covered_val:,.0f} of ${today_val:,.0f} "
          f"of today's cube ({cover*100:.1f}%)")
    if cover < MIN_VALUE_COVER:
        for lbl in unmatched + rejects:
            print(f"  missing: {lbl}")
        sys.exit(f"refusing to write: {(1-cover)*100:.1f}% of cube value has no history, "
                 f"which would show as a false {(1/cover-1)*100:.0f}% jump on {latest[:7]}. "
                 f"Re-run to retry (cached printings cost nothing).")

    rows.sort(key=lambda r: (r["date"], r["set"], r["number"], r["finish"]))
    tmp = track.PRICES_CSV + ".tmp"
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=track.FIELDS)
        w.writeheader()
        w.writerows(rows)
        w.writerows(existing)
    os.replace(tmp, track.PRICES_CSV)      # never leave a half-written history

    days = len({r["date"] for r in rows})
    print(f"backfilled {len(rows):,} rows over {days} days from {added} printings "
          f"({len(unmatched)} unmatched, {len(rejects)} rejected on disagreement); "
          f"history now starts {min(r['date'] for r in rows)}")


if __name__ == "__main__":
    main()
