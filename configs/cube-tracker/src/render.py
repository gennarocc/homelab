#!/usr/bin/env python3
"""Render prices.csv into a single self-contained dashboard.html.

No chart library: nginx on the Pi serves one file, and the page has to work
with no network at all. Sorting is the only JavaScript.
"""

import csv
import html
import json
import math
import os
from collections import defaultdict
from datetime import date as D

import images

WINDOWS = [("24h", 1), ("7d", 7), ("30d", 30), ("90d", 90), ("1y", 365)]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


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
            "key": key,
            "name": row["name"], "set": row["set"], "number": row["number"],
            "finish": row["finish"], "copies": copies,
            "was": was, "now": price,
            "delta": (price - was) * copies,
            "pct": (price - was) / was * 100,
        })
    return out


def nice_ticks(lo, hi, count=6):
    """Round axis values at a 1/2/2.5/5 step, so labels read as money."""
    span = hi - lo
    if span <= 0:
        span = max(abs(hi) * 0.02, 1.0)
        lo, hi = lo - span / 2, hi + span / 2
    raw = span / count
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if raw <= m * mag)
    start = math.floor(lo / step) * step
    ticks, v = [], start
    while v < hi + step:
        ticks.append(round(v, 6))
        v += step
    return ticks


def date_label(iso, show_year):
    d = D.fromisoformat(iso)
    return f"{MONTHS[d.month - 1]} {d.day}" + (f" '{d.year % 100:02d}" if show_year else "")


def chart(series):
    """Both series share one y-scale -- normalising each to its own range made
    them identical lines that hid each other, which is worse than no chart."""
    if len(series) < 2:
        return '<p class="muted">Two days of snapshots needed before the chart appears.</p>'

    w, h, L, R, T, B = 1400, 440, 104, 104, 26, 60
    dates = [s[0] for s in series]
    tot = [s[1] for s in series]
    idx = [s[2] for s in series]

    # Until the cube list actually changes, the day-one index *is* the total --
    # the seeded history applies today's list to every past day. Drawing a
    # second line exactly under the first just gives the legend a dead entry.
    same = all(abs(a - b) < 0.01 for a, b in zip(tot, idx))

    ticks = nice_ticks(min(min(tot), min(idx)), max(max(tot), max(idx)))
    ylo, yhi = ticks[0], ticks[-1]
    yspan = (yhi - ylo) or 1
    n = len(series)

    def X(i):
        return L + i * (w - L - R) / (n - 1)

    def Y(v):
        return h - B - (v - ylo) / yspan * (h - T - B)

    # Up to 10 date ticks, always including the first and last day.
    count = min(10, n)
    xi = sorted({round(k * (n - 1) / (count - 1)) for k in range(count)}) if count > 1 else [0]
    multiyear = D.fromisoformat(dates[-1]).year != D.fromisoformat(dates[0]).year

    parts = []
    for v in ticks:
        y = Y(v)
        parts.append(f'<line x1="{L}" y1="{y:.1f}" x2="{w - R}" y2="{y:.1f}" class="grid"/>')
        parts.append(f'<text x="{L - 12}" y="{y + 4:.1f}" class="tick end">${v:,.0f}</text>')
    for i in xi:
        x = X(i)
        parts.append(f'<line x1="{x:.1f}" y1="{T}" x2="{x:.1f}" y2="{h - B}" class="grid vgrid"/>')
        parts.append(f'<text x="{x:.1f}" y="{h - B + 24}" class="tick mid">'
                     f'{date_label(dates[i], multiyear)}</text>')

    def line(vals, color, fill):
        pts = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(vals))
        out = ""
        if fill:
            out += (f'<polygon points="{L},{h - B} {pts} {X(n - 1):.1f},{h - B}" '
                    f'fill="{color}" opacity=".12"/>')
        return out + f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2.5"/>'

    def endpoint(vals, color, cls, dy=0):
        x, y = X(n - 1), Y(vals[-1])
        return (f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.5" fill="{color}"/>'
                f'<text x="{x + 11:.1f}" y="{y + 4 + dy:.1f}" class="tick {cls}">'
                f'${vals[-1]:,.0f}</text>')

    svg = "".join(parts)
    svg += line(tot, "var(--accent)", True)
    if not same:
        svg += line(idx, "var(--accent2)", False)
    svg += endpoint(tot, "var(--accent)", "c1", 0)
    if not same:
        # Nudge the labels apart when the two lines end close together.
        gap = Y(idx[-1]) - Y(tot[-1])
        svg += endpoint(idx, "var(--accent2)", "c2",
                        (18 if gap >= 0 else -18) - gap if abs(gap) < 18 else 0)
    svg += (f'<line x1="{L}" y1="{h - B}" x2="{w - R}" y2="{h - B}" class="axis"/>'
            f'<line x1="{L}" y1="{T}" x2="{L}" y2="{h - B}" class="axis"/>')

    legend = '<p class="legend"><span class="key k1"></span>Total cube value'
    if same:
        legend += ('<span class="note">Same-card index tracks the total exactly '
                   'until the cube list changes.</span>')
    else:
        legend += '<span class="key k2"></span>Same-card index (day-one cards only)'

    return (
        f'<svg viewBox="0 0 {w} {h}" class="chart" '
        f'role="img" aria-label="Cube value over time">{svg}</svg>' + legend + '</p>'
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
                   f'<strong class="{cls}">{"+" if delta >= 0 else "−"}'
                   f'${abs(delta):,.0f}</strong>'
                   f'<span class="muted {cls}">{pct:+.1f}%</span></div>')
    return "".join(out)


def cube_name(data_dir):
    """track.py banks the cube's own name so the page titles itself."""
    try:
        with open(os.path.join(data_dir, "cube.json")) as f:
            return json.load(f).get("name") or "Cube Tracker"
    except Exception:
        return "Cube Tracker"


def build(csv_path, out_path):
    by_date, meta = load(csv_path)
    series = totals(by_date)
    dates = list(by_date)
    window_days = 30 if pick(dates, 30) else 7
    rows = movers(by_date, meta, window_days)

    latest = by_date[dates[-1]]
    mv = {m["key"]: m for m in rows}
    all_rows = []
    for k, (p, c) in latest.items():
        row = {"name": meta[k]["name"], "set": meta[k]["set"], "finish": meta[k]["finish"],
               "img": images.slug(meta[k]["set"], meta[k]["number"]),
               "copies": c, "price": round(p, 2), "value": round(p * c, 2)}
        m = mv.get(k)
        if m:   # cards added mid-window have no "then" price to compare against
            row.update(was=round(m["was"], 2), delta=round(m["delta"], 2),
                       pct=round(m["pct"], 2))
        all_rows.append(row)
    all_rows.sort(key=lambda r: -r["value"])
    table_json = json.dumps(all_rows, separators=(",", ":"))

    name = cube_name(os.path.dirname(os.path.abspath(csv_path)))
    page = TEMPLATE.format(
        title=html.escape(name),
        updated=dates[-1],
        cards=sum(c for _, c in latest.values()),
        printings=len(latest),
        stats=change_cards(series),
        chart=chart(series),
        window=f"{window_days}d",
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
<title>{title} &mdash; Cube Tracker</title>
<style>
/* Palette: categorical slots 1-2 for the two chart series, status green/red for
   gain/loss. Both stepped per surface so the small bold table numbers clear
   4.5:1 -- the reference green is only 3.27:1 on light, the red 3.62:1 on dark. */
:root{{--bg:#f4f4f2;--card:#fcfcfb;--ink:#0b0b0b;--muted:#52514e;--line:#e3e2de;
--accent:#2a78d6;--accent2:#eb6834;--up:#097f09;--down:#d03b3b;}}
@media(prefers-color-scheme:dark){{:root{{--bg:#111110;--card:#1a1a19;--ink:#fff;
--muted:#c3c2b7;--line:#383835;--accent:#3987e5;--accent2:#d95926;
--up:#0ca30c;--down:#e46a6a;}}}}
*{{box-sizing:border-box}}
[hidden]{{display:none!important}}
body{{margin:0;padding:28px 28px 80px;background:var(--bg);color:var(--ink);
font:16px/1.55 ui-sans-serif,-apple-system,"Segoe UI",sans-serif}}
main{{max-width:1760px;margin:0 auto}}
h1{{font-size:30px;margin:0 0 4px;letter-spacing:-.01em}}
h2{{font-size:19px;margin:38px 0 14px;color:var(--muted);text-transform:uppercase;
letter-spacing:.09em;display:flex;align-items:center;gap:14px;flex-wrap:wrap}}
h3{{font-size:16px;margin:0 0 12px;text-transform:uppercase;letter-spacing:.06em}}
h3.dim{{color:var(--muted)}}
.sub{{color:var(--muted);margin:0 0 22px;font-size:14px}}

.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));
gap:14px;margin-bottom:22px}}
.stat{{background:var(--card);border:1px solid var(--line);padding:14px 18px;
display:flex;flex-direction:column;gap:3px}}
.stat.big{{grid-column:span 2;min-width:260px}}
.stat .label{{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em}}
.stat strong{{font-size:34px;font-variant-numeric:tabular-nums;letter-spacing:-.02em;line-height:1.1}}
.stat.big strong{{font-size:46px}}
.stat .muted{{font-size:14px}}
@media(max-width:620px){{.stat strong{{font-size:26px}}.stat.big strong{{font-size:34px}}}}

.muted{{color:var(--muted);font-size:13px}}
.up{{color:var(--up)}} .down{{color:var(--down)}}
.panel{{background:var(--card);border:1px solid var(--line);padding:20px;overflow-x:auto}}
.chart{{width:100%;height:auto;min-height:280px;display:block}}
.grid{{stroke:var(--line);stroke-width:1}}
.vgrid{{opacity:.55}}
.axis{{stroke:var(--line);stroke-width:1.5}}
.tick{{fill:var(--muted);font-size:14px;font-variant-numeric:tabular-nums}}
.tick.end{{text-anchor:end}} .tick.mid{{text-anchor:middle}}
.tick.c1{{fill:var(--accent);font-weight:600}} .tick.c2{{fill:var(--accent2);font-weight:600}}
.legend{{margin:14px 0 0;font-size:14px;color:var(--muted);display:flex;align-items:center;gap:7px;flex-wrap:wrap}}
.key{{width:12px;height:12px;display:inline-block;margin-left:14px}}
.key:first-child{{margin-left:0}}
.k1{{background:var(--accent)}} .k2{{background:var(--accent2)}}
.legend .note{{margin-left:14px;font-style:italic;opacity:.8}}

/* Rank the movers by dollars or by percent -- which one matters depends on
   whether a card is cheap-and-spiking or expensive-and-drifting. */
.toggle{{display:inline-flex;border:1px solid var(--line);background:var(--card)}}
.toggle button{{font:600 14px/1 ui-sans-serif,-apple-system,sans-serif;color:var(--muted);
background:none;border:0;padding:7px 16px;cursor:pointer;letter-spacing:.04em}}
.toggle button+button{{border-left:1px solid var(--line)}}
.toggle button.on{{background:var(--accent);color:#fff}}
.floor{{font-size:13px;font-weight:400;text-transform:none;letter-spacing:0;opacity:.85}}

.cols{{display:grid;grid-template-columns:repeat(2,1fr);gap:16px}}
@media(max-width:900px){{.cols{{grid-template-columns:1fr}}}}
table{{width:100%;border-collapse:collapse;font-size:15px}}
th,td{{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);white-space:nowrap}}
th{{color:var(--muted);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.06em}}
#all th{{cursor:pointer}}
td.num,th.num{{text-align:right;font-variant-numeric:tabular-nums}}
td.money{{font-weight:650}}
td em{{color:var(--accent2);font-style:normal;font-size:12px}}
tbody tr:last-child td{{border-bottom:0}}
#all td:first-child{{white-space:normal}}
.scroll{{max-height:70vh;overflow:auto}}
#all thead th{{position:sticky;top:0;z-index:2;background:var(--card);
box-shadow:inset 0 -1px 0 var(--line)}}
td[data-img]{{cursor:default}}

/* Hover art. One <img> reused for every card, positioned at the cursor. */
#preview{{position:fixed;width:340px;max-width:46vw;z-index:60;display:none;
opacity:0;transition:opacity .09s ease-out;pointer-events:none;
box-shadow:0 14px 38px rgba(0,0,0,.5)}}
#preview.on{{display:block;opacity:1}}
@media(hover:none){{#preview{{display:none!important}}}}
</style></head><body><main>
<h1>{title}</h1>
<p class="sub">{printings} printings &middot; {cards} cards &middot; TCGplayer market, snapshot {updated}</p>
<div class="stats">{stats}</div>
<div class="panel">{chart}</div>

<h2>Gainers &amp; losers &mdash; last {window}
  <span class="toggle" role="group" aria-label="Rank movers by">
    <button type="button" data-metric="delta">$</button>
    <button type="button" data-metric="pct">%</button>
  </span>
  <span class="floor" id="floor" hidden>cards under $2 excluded</span>
</h2>
<div class="cols">
<div class="panel"><h3 id="gain-h"></h3><div id="gain"></div></div>
<div class="panel"><h3 id="loss-h"></h3><div id="loss"></div></div>
</div>

<h2>Every card</h2>
<div class="panel"><div class="scroll"><table id="all"><thead><tr>
<th data-k="name">Card</th><th data-k="set">Set</th>
<th class="num" data-k="copies">Qty</th><th class="num" data-k="price">Price</th>
<th class="num" data-k="value">Value</th></tr></thead><tbody></tbody></table></div></div>

<img id="preview" alt="">
<script>
const rows = {table_json};
const esc = s => String(s).replace(/[&<>]/g, c => ({{"&":"&amp;","<":"&lt;",">":"&gt;"}}[c]));
const money = v => (v < 0 ? "\u2212$" : "+$") + Math.abs(v).toFixed(2);
const pct = v => (v < 0 ? "\u2212" : "+") + Math.abs(v).toFixed(1) + "%";
const cell = r => `<td data-img="images/${{encodeURIComponent(r.img)}}.jpg">${{esc(r.name)}}` +
  `${{r.finish === "foil" ? ' <em>foil</em>' : ""}}<span class="muted"> ${{esc(r.set)}}</span>` +
  `${{r.copies > 1 ? ` <span class="muted">x${{r.copies}}</span>` : ""}}</td>`;

/* ---- hover art ---- */
const pv = document.getElementById("preview");
const PW = 340, PH = 475;       // 488x680 art, scaled to the CSS width
let pvsrc = "";
function place(e) {{
  const w = Math.min(PW, innerWidth * .46), h = w * PH / PW;
  let x = e.clientX + 22, y = e.clientY - h / 2;
  if (x + w > innerWidth - 12) x = e.clientX - w - 22;   // flip to the left edge
  pv.style.left = Math.max(12, x) + "px";
  pv.style.top = Math.min(Math.max(12, y), innerHeight - h - 12) + "px";
}}
pv.onerror = () => pv.classList.remove("on");   // a printing we failed to fetch
document.addEventListener("mouseover", e => {{
  const el = e.target.closest("[data-img]");
  if (!el) return;
  if (el.dataset.img !== pvsrc) {{ pvsrc = el.dataset.img; pv.src = pvsrc; }}
  place(e); pv.classList.add("on");
}});
document.addEventListener("mousemove", e => {{ if (pv.classList.contains("on")) place(e); }});
document.addEventListener("mouseout", e => {{
  if (e.target.closest("[data-img]")) pv.classList.remove("on");
}});
addEventListener("scroll", () => pv.classList.remove("on"),
                 {{passive: true, capture: true}});   // capture: inner scrollers too

/* ---- gainers & losers, rankable by $ or % ---- */
const moved = rows.filter(r => r.was !== undefined);
let metric = "delta";
try {{ metric = localStorage.getItem("cube.metric") || "delta"; }} catch (e) {{}}

function moverTable(sign) {{
  // A $0.40 card going to $1.20 is +200% and means nothing, so percent mode
  // keeps the same >=$2 floor the dollar ranking doesn't need.
  const pool = metric === "pct" ? moved.filter(r => r.was >= 2) : moved;
  const top = [...pool].sort((a, b) => sign * (b[metric] - a[metric])).slice(0, 12);
  if (!top.length) return '<p class="muted">Not enough history yet.</p>';
  return '<table><thead><tr><th>Card</th><th class="num">Then</th><th class="num">Now</th>' +
    '<th class="num">Change</th><th class="num">%</th></tr></thead><tbody>' +
    top.map(r => {{
      const t = r.delta >= 0 ? "up" : "down";
      return `<tr>${{cell(r)}}<td class="num">$${{r.was.toFixed(2)}}</td>` +
        `<td class="num">$${{r.price.toFixed(2)}}</td>` +
        `<td class="num money ${{t}}">${{money(r.delta)}}</td>` +
        `<td class="num ${{t}}">${{pct(r.pct)}}</td></tr>`;
    }}).join("") + "</tbody></table>";
}}
function drawMovers() {{
  const byMoney = metric === "delta";
  gainH.textContent = byMoney ? "Biggest $ gain" : "Top % gain";
  lossH.textContent = byMoney ? "Biggest $ drop" : "Top % drop";
  document.getElementById("gain").innerHTML = moverTable(1);
  document.getElementById("loss").innerHTML = moverTable(-1);
  document.getElementById("floor").hidden = byMoney;
  document.querySelectorAll(".toggle button").forEach(b =>
    b.classList.toggle("on", b.dataset.metric === metric));
}}
const gainH = document.getElementById("gain-h"), lossH = document.getElementById("loss-h");
document.querySelectorAll(".toggle button").forEach(b => b.onclick = () => {{
  metric = b.dataset.metric;
  try {{ localStorage.setItem("cube.metric", metric); }} catch (e) {{}}
  drawMovers();
}});
drawMovers();

/* ---- every card ---- */
let dir = -1, key = "value";
function draw() {{
  const sorted = [...rows].sort((a, b) =>
    typeof a[key] === "string" ? dir * a[key].localeCompare(b[key]) : dir * (a[key] - b[key]));
  document.querySelector("#all tbody").innerHTML = sorted.map(r =>
    `<tr>${{cell(r)}}<td>${{esc(r.set)}}</td><td class="num">${{r.copies}}</td>` +
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
