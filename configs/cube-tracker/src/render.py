#!/usr/bin/env python3
"""Render prices.csv into a single self-contained dashboard.html.

No chart library: nginx on the Pi serves one file, and the page has to work
with no network at all. Sorting is the only JavaScript.
"""

import csv
import html
import json
import os
from collections import defaultdict

WINDOWS = [("24h", 1), ("7d", 7), ("30d", 30), ("90d", 90), ("1y", 365)]


def load(path):
    by_date = defaultdict(dict)
    meta = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            key = (row["uuid"], row["finish"])
            by_date[row["date"]][key] = (float(row["price"]), int(row["copies"]))
            meta[key] = row
    return dict(sorted(by_date.items())), meta


def totals(by_date):
    """Headline value, plus an index over only the cards held since day one.

    The two diverge whenever the cube list changes, which is the point: the
    index says what prices did, the total says what the cube is worth.
    """
    dates = list(by_date)
    baseline = set(by_date[dates[0]]) if dates else set()
    series = []
    for d in dates:
        snap = by_date[d]
        total = sum(p * c for p, c in snap.values())
        index = sum(p * c for k, (p, c) in snap.items() if k in baseline)
        series.append((d, total, index))
    return series


def pick(dates, days_back):
    """Nearest stored date at or before `days_back` days before the last one."""
    from datetime import date as D

    if not dates:
        return None
    last = D.fromisoformat(dates[-1])
    target = last.toordinal() - days_back
    candidates = [d for d in dates if D.fromisoformat(d).toordinal() <= target]
    return candidates[-1] if candidates else None


def movers(by_date, meta, days_back, limit=15):
    dates = list(by_date)
    start = pick(dates, days_back)
    if not start:
        return []
    a, b = by_date[start], by_date[dates[-1]]
    out = []
    for key, (price, copies) in b.items():
        if key not in a:
            continue
        was = a[key][0]
        if was <= 0:
            continue
        row = meta[key]
        out.append({
            "name": row["name"], "set": row["set"], "number": row["number"],
            "finish": row["finish"], "copies": copies,
            "was": was, "now": price,
            "delta": (price - was) * copies,
            "pct": (price - was) / was * 100,
        })
    return out


def sparkline(series, idx, color, w=860, h=220, pad=34):
    vals = [s[idx] for s in series]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1
    step = (w - pad * 2) / (len(vals) - 1)
    pts = [
        (pad + i * step, h - pad - (v - lo) / span * (h - pad * 2))
        for i, v in enumerate(vals)
    ]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area = f"{pad},{h - pad} {line} {pts[-1][0]:.1f},{h - pad}"
    return (
        f'<polygon points="{area}" fill="{color}" opacity=".10"/>'
        f'<polyline points="{line}" fill="none" stroke="{color}" stroke-width="2"/>'
    )


def chart(series):
    if len(series) < 2:
        return '<p class="muted">Two days of snapshots needed before the chart appears.</p>'
    w, h, pad = 860, 220, 34
    vals = [s[1] for s in series]
    lo, hi = min(vals), max(vals)
    grid = "".join(
        f'<line x1="{pad}" y1="{y:.0f}" x2="{w - pad}" y2="{y:.0f}" class="grid"/>'
        for y in [pad, h / 2, h - pad]
    )
    labels = (
        f'<text x="{pad}" y="{pad - 10}" class="tick">${hi:,.0f}</text>'
        f'<text x="{pad}" y="{h - pad + 18}" class="tick">${lo:,.0f}</text>'
        f'<text x="{w - pad}" y="{h - pad + 18}" class="tick end">{series[-1][0]}</text>'
        f'<text x="{pad}" y="{h - pad + 32}" class="tick">{series[0][0]}</text>'
    )
    return (
        f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" '
        f'aria-label="Cube value over time">{grid}'
        + sparkline(series, 1, "var(--accent)")
        + sparkline(series, 2, "var(--accent2)")
        + labels
        + "</svg>"
        '<p class="legend"><span class="key k1"></span>Total cube value'
        '<span class="key k2"></span>Same-card index (day-one cards only)</p>'
    )


def change_cards(series):
    dates = [s[0] for s in series]
    now = series[-1][1]
    out = [f'<div class="stat big"><span class="label">Cube value</span>'
           f'<strong>${now:,.2f}</strong><span class="muted">{dates[-1]}</span></div>']
    for label, days in WINDOWS:
        start = pick(dates, days)
        if not start:
            continue
        was = next(s[1] for s in series if s[0] == start)
        delta, pct = now - was, (now - was) / was * 100 if was else 0
        cls = "up" if delta >= 0 else "down"
        out.append(f'<div class="stat"><span class="label">{label}</span>'
                   f'<strong class="{cls}">{delta:+,.0f}</strong>'
                   f'<span class="muted {cls}">{pct:+.1f}%</span></div>')
    return "".join(out)


def mover_table(rows, key, reverse, limit, money):
    rows = sorted(rows, key=lambda r: r[key], reverse=reverse)[:limit]
    if not rows:
        return '<p class="muted">Not enough history yet.</p>'
    cells = []
    for r in rows:
        change = "${:+,.2f}".format(r["delta"]) if money else "{:+.1f}%".format(r["pct"])
        foil = " <em>foil</em>" if r["finish"] == "foil" else ""
        trend = "up" if r[key] >= 0 else "down"
        cells.append(
            '<tr><td>{name}{foil}<span class="muted"> {set}</span></td>'
            '<td class="num">${was:,.2f}</td><td class="num">${now:,.2f}</td>'
            '<td class="num {trend}">{change}</td></tr>'.format(
                name=html.escape(r["name"]), foil=foil, set=html.escape(r["set"]),
                was=r["was"], now=r["now"], trend=trend, change=change)
        )
    body = "".join(cells)
    return ('<table><thead><tr><th>Card</th><th class="num">Then</th>'
            '<th class="num">Now</th><th class="num">Change</th></tr></thead>'
            f"<tbody>{body}</tbody></table>")


def build(csv_path, out_path):
    by_date, meta = load(csv_path)
    series = totals(by_date)
    dates = list(by_date)
    window_days = 30 if pick(dates, 30) else 7
    rows = movers(by_date, meta, window_days)

    latest = by_date[dates[-1]]
    all_rows = sorted(
        ({"name": meta[k]["name"], "set": meta[k]["set"], "finish": meta[k]["finish"],
          "copies": c, "price": p, "value": p * c} for k, (p, c) in latest.items()),
        key=lambda r: -r["value"],
    )
    table_json = json.dumps(all_rows)

    page = TEMPLATE.format(
        updated=dates[-1],
        cards=sum(c for _, c in latest.values()),
        printings=len(latest),
        stats=change_cards(series),
        chart=chart(series),
        window=f"{window_days}d",
        gain_pct=mover_table([r for r in rows if r["was"] >= 2], "pct", True, 10, False),
        loss_pct=mover_table([r for r in rows if r["was"] >= 2], "pct", False, 10, False),
        gain_usd=mover_table(rows, "delta", True, 10, True),
        loss_usd=mover_table(rows, "delta", False, 10, True),
        table_json=table_json,
    )
    tmp = out_path + ".tmp"
    with open(tmp, "w") as f:
        f.write(page)
    os.replace(tmp, out_path)  # nginx never sees a half-written page
    print(f"wrote {out_path} ({len(page) // 1024} KB)")


TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cube Tracker</title>
<style>
:root{{--bg:#faf8f5;--card:#fff;--ink:#1a1714;--muted:#6b635a;--line:#e5ded4;
--accent:#8a5a2b;--accent2:#5a7d8a;--up:#1f7a4d;--down:#a8362f;}}
@media(prefers-color-scheme:dark){{:root{{--bg:#16140f;--card:#201d17;--ink:#f0ebe3;
--muted:#9c938a;--line:#322d25;--accent:#d49a5a;--accent2:#7fb0c0;--up:#4ec98a;--down:#e8705f;}}}}
*{{box-sizing:border-box}}
body{{margin:0;padding:24px 16px 64px;background:var(--bg);color:var(--ink);
font:15px/1.5 ui-sans-serif,-apple-system,"Segoe UI",sans-serif}}
main{{max-width:900px;margin:0 auto}}
h1{{font-size:22px;margin:0 0 2px}}
h2{{font-size:15px;margin:28px 0 10px;color:var(--muted);text-transform:uppercase;letter-spacing:.07em}}
.sub{{color:var(--muted);margin:0 0 20px;font-size:13px}}
.stats{{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:20px}}
.stat{{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:10px 14px;min-width:92px;display:flex;flex-direction:column;gap:1px}}
.stat.big{{min-width:190px}}
.stat .label{{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}}
.stat strong{{font-size:19px;font-variant-numeric:tabular-nums}}
.stat.big strong{{font-size:26px}}
.muted{{color:var(--muted);font-size:12px}}
.up{{color:var(--up)}} .down{{color:var(--down)}}
.panel{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;overflow-x:auto}}
.chart{{width:100%;height:auto;display:block}}
.grid{{stroke:var(--line);stroke-width:1}}
.tick{{fill:var(--muted);font-size:11px}} .tick.end{{text-anchor:end}}
.legend{{margin:10px 0 0;font-size:12px;color:var(--muted);display:flex;align-items:center;gap:6px;flex-wrap:wrap}}
.key{{width:11px;height:11px;border-radius:3px;display:inline-block;margin-left:10px}}
.key:first-child{{margin-left:0}}
.k1{{background:var(--accent)}} .k2{{background:var(--accent2)}}
.cols{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}
@media(max-width:700px){{.cols{{grid-template-columns:1fr}}}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th,td{{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);white-space:nowrap}}
th{{color:var(--muted);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.05em;cursor:pointer}}
td.num,th.num{{text-align:right;font-variant-numeric:tabular-nums}}
td em{{color:var(--accent);font-style:normal;font-size:11px}}
tbody tr:last-child td{{border-bottom:0}}
#all td:first-child{{white-space:normal}}
</style></head><body><main>
<h1>Cube Tracker</h1>
<p class="sub">{printings} printings &middot; {cards} cards &middot; TCGplayer market, snapshot {updated}</p>
<div class="stats">{stats}</div>
<div class="panel">{chart}</div>

<h2>Gainers &amp; losers &mdash; last {window}</h2>
<div class="cols">
<div class="panel"><h3 class="muted">Top % gain (&ge;$2)</h3>{gain_pct}</div>
<div class="panel"><h3 class="muted">Top % drop (&ge;$2)</h3>{loss_pct}</div>
<div class="panel"><h3 class="muted">Top $ gain</h3>{gain_usd}</div>
<div class="panel"><h3 class="muted">Top $ drop</h3>{loss_usd}</div>
</div>

<h2>Every card</h2>
<div class="panel"><table id="all"><thead><tr>
<th data-k="name">Card</th><th data-k="set">Set</th>
<th class="num" data-k="copies">Qty</th><th class="num" data-k="price">Price</th>
<th class="num" data-k="value">Value</th></tr></thead><tbody></tbody></table></div>

<script>
const rows = {table_json};
let dir = -1, key = "value";
const esc = s => String(s).replace(/[&<>]/g, c => ({{"&":"&amp;","<":"&lt;",">":"&gt;"}}[c]));
function draw() {{
  const sorted = [...rows].sort((a, b) =>
    typeof a[key] === "string" ? dir * a[key].localeCompare(b[key]) : dir * (a[key] - b[key]));
  document.querySelector("#all tbody").innerHTML = sorted.map(r =>
    `<tr><td>${{esc(r.name)}}${{r.finish === "foil" ? ' <em>foil</em>' : ""}}</td>` +
    `<td>${{esc(r.set)}}</td><td class="num">${{r.copies}}</td>` +
    `<td class="num">$${{r.price.toFixed(2)}}</td>` +
    `<td class="num">$${{r.value.toFixed(2)}}</td></tr>`).join("");
}}
document.querySelectorAll("#all th").forEach(th => th.onclick = () => {{
  const k = th.dataset.k;
  dir = k === key ? -dir : (k === "name" || k === "set" ? 1 : -1);
  key = k; draw();
}});
draw();
</script>
</main></body></html>
"""


if __name__ == "__main__":
    import sys

    data_dir = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
    build(sys.argv[1] if len(sys.argv) > 1 else os.path.join(data_dir, "prices.csv"),
          sys.argv[2] if len(sys.argv) > 2 else os.path.join(data_dir, "dashboard.html"))
