#!/usr/bin/env python3
"""Cache a card image per printing so the dashboard can show art on hover.

Driven by the live cube list, not by set: the cube holds promo and foreign
printings (F02, G03, PAL02, FBB, REN) that no "Beta through Onslaught" set
filter would catch, and bulk-fetching those 42 sets would mean ~10k images to
use 489 of them.

Scryfall asks that images be cached rather than hotlinked, which suits a page
that has to render with no network anyway. Images are immutable once fetched;
a printing's art never changes.

Stdlib only. Safe to re-run: existing files are never re-fetched.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

COLLECTION_URL = "https://api.scryfall.com/cards/collection"
SIZE = "normal"          # 488x680 -- `small` is too soft for a hover preview
BATCH = 75               # Scryfall's documented maximum per collection request
DELAY = 0.1              # Scryfall asks for 50-100ms between requests
UA = "cube-tracker/1.0 (personal homelab; one cube, cached locally)"


def slug(set_code, number):
    """Filename for a printing, derived from data already in prices.csv.

    Collector numbers carry things like '80d' and the odd star, so anything
    outside ASCII alphanumerics is encoded rather than stripped -- stripping
    would let two different printings collide on one filename.
    """
    safe = "".join(c if (c.isascii() and c.isalnum()) else f"_{ord(c):x}_"
                   for c in str(number))
    return f"{set_code.upper()}-{safe}"


def _get(url, data=None):
    req = urllib.request.Request(url, data=data, headers={
        "User-Agent": UA, "Accept": "application/json",
        "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def _image_url(card):
    uris = card.get("image_uris") or {}
    if not uris:  # split and double-faced cards carry art per face
        faces = card.get("card_faces") or []
        uris = (faces[0].get("image_uris") or {}) if faces else {}
    return uris.get(SIZE)


def sync(entries, dest, log=print):
    """Fetch any missing art for `entries` (track.py's cube dict). Returns
    (added, failed). Never raises -- art is cosmetic, the snapshot is not."""
    os.makedirs(dest, exist_ok=True)
    wanted = {}
    for e in entries.values():
        if e.get("scryfall_id") and e.get("set"):
            wanted[slug(e["set"], e["number"])] = e["scryfall_id"]

    missing = {s: i for s, i in wanted.items()
               if not os.path.exists(os.path.join(dest, s + ".jpg"))}
    if not missing:
        return 0, 0

    by_id = {i: s for s, i in missing.items()}
    ids = list(by_id)
    log(f"images: fetching {len(ids)} of {len(wanted)} printings")

    added = failed = 0
    for start in range(0, len(ids), BATCH):
        chunk = ids[start:start + BATCH]
        try:
            res = _get(COLLECTION_URL,
                       json.dumps({"identifiers": [{"id": i} for i in chunk]}).encode())
        except Exception as exc:
            print(f"warn: scryfall lookup failed: {exc}", file=sys.stderr)
            failed += len(chunk)
            continue
        time.sleep(DELAY)

        for card in res.get("data", []):
            name = by_id.get(card["id"])
            url = _image_url(card)
            if not name or not url:
                failed += 1
                continue
            path = os.path.join(dest, name + ".jpg")
            tmp = path + ".tmp"
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
                    f.write(r.read())
                os.replace(tmp, path)   # nginx never sees a half-written image
                added += 1
            except Exception as exc:
                print(f"warn: {name}: {exc}", file=sys.stderr)
                failed += 1
                if os.path.exists(tmp):
                    os.unlink(tmp)
            time.sleep(DELAY)

        failed += len(res.get("not_found", []))

    return added, failed


if __name__ == "__main__":
    import track

    cube = track.load_cube()
    added, failed = sync(cube, os.path.join(track.DATA_DIR, "images"))
    print(f"images: {added} added, {failed} failed")
