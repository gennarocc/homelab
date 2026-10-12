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
from datetime import date as D

import images

WINDOWS = [("24h", 1), ("7d", 7), ("30d", 30), ("90d", 90), ("1y", 365)]

# Moderately-played haircuts. CONTEXT.md records these as ranges from a one-off
# estimate; these are the midpoints, and they are the one set of numbers here
# that are judgement rather than measurement -- tune them in this table alone.
#
# Revised-through-Weatherlight was never given a range, yet it is 100 cards and
# $7.6k of this cube. It sits between the two documented bands, so it takes the
# value between them. Flagged rather than hidden.
MP_FOIL = 0.65          # 58-72%
MP_CHEAP = 0.90         # 85-95%; a sub-$1 card barely discounts by condition
MP_BANDS = [("1994-04-11", 0.66),   # pre-Revised           60-72%
            ("1997-10-14", 0.72),   # Revised..Weatherlight  UNDOCUMENTED
            ("2003-06-01", 0.77)]   # Tempest..Scourge      72-82%
MP_DEFAULT = 0.77


def mp_factor(release, finish, price):
    """Cheapest first: condition barely moves a bulk card, foil damage is its
    own thing regardless of era, and era is the fallback."""
    if price < 1:
        return MP_CHEAP
    if finish == "foil":
        return MP_FOIL
    for cutoff, factor in MP_BANDS:
        if release and release < cutoff:
            return factor
    return MP_DEFAULT


def load(path):
    by_date = defaultdict(dict)
    meta = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            key = (row["uuid"], row["finish"])
            by_date[row["date"]][key] = (float(row["price"]), int(row["copies"]))
            meta[key] = row
    return dict(sorted(by_date.items())), meta


def totals(by_date, meta, setdates):
    """Per day: (date, total NM, index NM, total MP, index MP).

    The index covers only cards held since day one. It and the total diverge
    whenever the cube list changes, which is the point: the index says what
    prices did, the total says what the cube is worth.
    """
    dates = list(by_date)
    baseline = set(by_date[dates[0]]) if dates else set()
    series = []
    for d in dates:
        tn = ti = mn = mi = 0.0
        for k, (p, c) in by_date[d].items():
            m = meta[k]
            f = mp_factor(setdates.get(m["set"], ""), m["finish"], p)
            tn += p * c
            mn += p * f * c
            if k in baseline:
                ti += p * c
                mi += p * f * c
        series.append((d, tn, ti, mn, mi))
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


def change_cards(series, ti=1):
    dates = [s[0] for s in series]
    now = series[-1][ti]
    out = [f'<div class="stat big"><span class="label">Cube value</span>'
           f'<strong>${now:,.2f}</strong><span class="muted">{dates[-1]}</span></div>']
    for label, days in WINDOWS:
        start = pick(dates, days)
        if not start:
            continue
        was = next(s[ti] for s in series if s[0] == start)
        delta, pct = now - was, (now - was) / was * 100 if was else 0
        cls = "up" if delta >= 0 else "down"
        out.append(f'<div class="stat"><span class="label">{label}</span>'
                   f'<strong class="{cls}">{"+" if delta >= 0 else "−"}'
                   f'${abs(delta):,.0f}</strong>'
                   f'<span class="muted {cls}">{pct:+.1f}%</span></div>')
    return "".join(out)


def cube_meta(data_dir):
    """track.py banks the cube's name and its sets' release dates."""
    try:
        with open(os.path.join(data_dir, "cube.json")) as f:
            d = json.load(f)
        return d.get("name") or "Cube Tracker", d.get("sets") or {}
    except Exception:
        return "Cube Tracker", {}


def build(csv_path, out_path):
    by_date, meta = load(csv_path)
    name, setdates = cube_meta(os.path.dirname(os.path.abspath(csv_path)))
    series = totals(by_date, meta, setdates)
    dates = list(by_date)
    window_days = 30 if pick(dates, 30) else 7
    rows = movers(by_date, meta, window_days)

    latest = by_date[dates[-1]]
    mv = {m["key"]: m for m in rows}
    all_rows = []
    for k, (p, c) in latest.items():
        row = {"name": meta[k]["name"], "set": meta[k]["set"], "finish": meta[k]["finish"],
               "img": images.slug(meta[k]["set"], meta[k]["number"]),
               "copies": c, "price": round(p, 2), "value": round(p * c, 2),
               "mp": mp_factor(setdates.get(meta[k]["set"], ""), meta[k]["finish"], p)}
        m = mv.get(k)
        if m:   # cards added mid-window have no "then" price to compare against
            row.update(was=round(m["was"], 2), delta=round(m["delta"], 2),
                       pct=round(m["pct"], 2))
        all_rows.append(row)
    all_rows.sort(key=lambda r: -r["value"])
    table_json = json.dumps(all_rows, separators=(",", ":"))

    page = TEMPLATE.format(
        title=html.escape(name),
        updated=dates[-1],
        cards=sum(c for _, c in latest.values()),
        printings=len(latest),
        stats_nm=change_cards(series, 1),
        stats_mp=change_cards(series, 3),
        series_json=json.dumps([[d, round(a, 2), round(b, 2), round(c, 2), round(e, 2)]
                                for d, a, b, c, e in series], separators=(",", ":")),
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
.chartbar{{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:14px}}
.top{{display:flex;justify-content:space-between;align-items:flex-start;
gap:20px;flex-wrap:wrap}}
.controls{{display:flex;align-items:center;gap:12px;flex-wrap:wrap}}
.btn{{font:600 14px/1 ui-sans-serif,-apple-system,sans-serif;color:var(--ink);
background:var(--card);border:1px solid var(--line);padding:8px 16px;cursor:pointer}}
.btn:hover{{border-color:var(--accent);color:var(--accent)}}
.btn[disabled]{{opacity:.55;cursor:default;border-color:var(--line);color:var(--muted)}}
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
<header class="top">
  <div>
    <h1>{title}</h1>
    <p class="sub">{printings} printings &middot; {cards} cards &middot;
       TCGplayer market, snapshot {updated}</p>
  </div>
  <div class="controls">
    <span class="toggle" id="cond" role="group" aria-label="Card condition">
      <button type="button" data-cond="nm">Near mint</button>
      <button type="button" data-cond="mp">Mod. played</button>
    </span>
    <button type="button" id="refresh" class="btn">Refresh data</button>
    <span class="floor" id="refresh-msg" hidden></span>
  </div>
</header>
<div class="stats" id="stats-nm">{stats_nm}</div>
<div class="stats" id="stats-mp" hidden>{stats_mp}</div>
<div class="panel">
<div class="chartbar">
  <span class="toggle" id="range" role="group" aria-label="Chart range">
    <button type="button" data-days="30">30 days</button>
    <button type="button" data-days="90">90 days</button>
    <button type="button" data-days="365">1 year</button>
    <button type="button" data-days="0">All time</button>
  </span>
  <span class="floor" id="range-note"></span>
</div>
<div id="chart"></div>
<p class="legend" id="legend"></p>
<p class="legend"><span class="note" id="mp-note" hidden>Moderately-played
estimate: 66% of NM pre-Revised, 72% Revised&ndash;Weatherlight, 77%
Tempest&ndash;Scourge, 65% foils, 90% under $1. Percentages are judgement, not
market data.</span></p></div>

<h2>Gainers &amp; losers &mdash; last {window}
  <span class="toggle" id="metric" role="group" aria-label="Rank movers by">
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

/* ---- the chart ----
   Drawn here rather than server-side: four ranges x two conditions would
   otherwise be eight SVGs baked into the page. */
const series = {series_json};
const MON = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
let days = 90;
try {{ days = +(localStorage.getItem("cube.days") ?? 90); }} catch (e) {{}}

function niceTicks(lo, hi, count) {{
  let span = hi - lo;
  if (span <= 0) {{ span = Math.max(Math.abs(hi) * .02, 1); lo -= span / 2; hi += span / 2; }}
  const raw = span / count, mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(v => raw <= v);
  const out = []; for (let v = Math.floor(lo / step) * step; v < hi + step; v += step) out.push(v);
  return out;
}}
const usd = v => "$" + Math.round(v).toLocaleString();
function dlabel(iso, yr) {{
  const [y, m, d] = iso.split("-");
  return MON[+m - 1] + " " + (+d) + (yr ? " '" + y.slice(2) : "");
}}
function drawChart() {{
  const win = days > 0 ? series.slice(-days) : series;
  const ti = cond === "mp" ? 3 : 1, ii = cond === "mp" ? 4 : 2;
  const box = document.getElementById("chart"), leg = document.getElementById("legend");
  document.getElementById("range-note").textContent =
    days > 0 && series.length <= days ? "only " + series.length + " days banked so far" : "";
  document.querySelectorAll("#range button").forEach(b =>
    b.classList.toggle("on", +b.dataset.days === days));
  if (win.length < 2) {{
    box.innerHTML = '<p class="muted">Two days of snapshots needed before the chart appears.</p>';
    leg.innerHTML = ""; return;
  }}
  const W = 1400, H = 440, L = 104, R = 104, T = 26, B = 60, n = win.length;
  const tot = win.map(r => r[ti]), idx = win.map(r => r[ii]);
  const same = tot.every((v, i) => Math.abs(v - idx[i]) < 0.01);
  const ticks = niceTicks(Math.min(...tot, ...idx), Math.max(...tot, ...idx), 6);
  const ylo = ticks[0], yspan = (ticks[ticks.length - 1] - ylo) || 1;
  const X = i => L + i * (W - L - R) / (n - 1);
  const Y = v => H - B - (v - ylo) / yspan * (H - T - B);
  let g = "";
  for (const v of ticks) {{
    const y = Y(v);
    g += `<line x1="${{L}}" y1="${{y.toFixed(1)}}" x2="${{W - R}}" y2="${{y.toFixed(1)}}" class="grid"/>` +
         `<text x="${{L - 12}}" y="${{(y + 4).toFixed(1)}}" class="tick end">${{usd(v)}}</text>`;
  }}
  const count = Math.min(10, n), yr = win[0][0].slice(0, 4) !== win[n - 1][0].slice(0, 4);
  const seen = new Set();
  for (let k = 0; k < count; k++) {{
    const i = Math.round(k * (n - 1) / (count - 1 || 1));
    if (seen.has(i)) continue; seen.add(i);
    const x = X(i);
    g += `<line x1="${{x.toFixed(1)}}" y1="${{T}}" x2="${{x.toFixed(1)}}" y2="${{H - B}}" class="grid vgrid"/>` +
         `<text x="${{x.toFixed(1)}}" y="${{H - B + 24}}" class="tick mid">${{dlabel(win[i][0], yr)}}</text>`;
  }}
  const path = v => v.map((p, i) => `${{X(i).toFixed(1)}},${{Y(p).toFixed(1)}}`).join(" ");
  g += `<polygon points="${{L}},${{H - B}} ${{path(tot)}} ${{X(n - 1).toFixed(1)}},${{H - B}}" fill="var(--accent)" opacity=".12"/>`;
  g += `<polyline points="${{path(tot)}}" fill="none" stroke="var(--accent)" stroke-width="2.5"/>`;
  if (!same) g += `<polyline points="${{path(idx)}}" fill="none" stroke="var(--accent2)" stroke-width="2.5"/>`;
  const dot = (vals, col, cls, dy) =>
    `<circle cx="${{X(n - 1).toFixed(1)}}" cy="${{Y(vals[n - 1]).toFixed(1)}}" r="4.5" fill="${{col}}"/>` +
    `<text x="${{(X(n - 1) + 11).toFixed(1)}}" y="${{(Y(vals[n - 1]) + 4 + dy).toFixed(1)}}" class="tick ${{cls}}">${{usd(vals[n - 1])}}</text>`;
  g += dot(tot, "var(--accent)", "c1", 0);
  if (!same) {{
    const gap = Y(idx[n - 1]) - Y(tot[n - 1]);
    g += dot(idx, "var(--accent2)", "c2", Math.abs(gap) < 18 ? (gap >= 0 ? 18 : -18) - gap : 0);
  }}
  g += `<line x1="${{L}}" y1="${{H - B}}" x2="${{W - R}}" y2="${{H - B}}" class="axis"/>` +
       `<line x1="${{L}}" y1="${{T}}" x2="${{L}}" y2="${{H - B}}" class="axis"/>`;
  box.innerHTML = `<svg viewBox="0 0 ${{W}} ${{H}}" class="chart" role="img" aria-label="Cube value over time">${{g}}</svg>`;
  leg.innerHTML = '<span class="key k1"></span>Total cube value' + (same
    ? '<span class="note">Same-card index tracks the total exactly until the cube list changes.</span>'
    : '<span class="key k2"></span>Same-card index (day-one cards only)');
}}
document.querySelectorAll("#range button").forEach(b => b.onclick = () => {{
  days = +b.dataset.days;
  try {{ localStorage.setItem("cube.days", days); }} catch (e) {{}}
  drawChart();
}});

/* ---- near mint vs moderately played ---- */
let cond = "nm";
try {{ cond = localStorage.getItem("cube.cond") || "nm"; }} catch (e) {{}}
const fac = r => cond === "mp" ? r.mp : 1;
function applyCond() {{
  const mp = cond === "mp";
  document.getElementById("stats-nm").hidden = mp;
  document.getElementById("stats-mp").hidden = !mp;
  document.getElementById("mp-note").hidden = !mp;
  drawChart();
  document.querySelectorAll("#cond button").forEach(b =>
    b.classList.toggle("on", b.dataset.cond === cond));
  drawMovers(); draw();
}}
document.querySelectorAll("#cond button").forEach(b => b.onclick = () => {{
  cond = b.dataset.cond;
  try {{ localStorage.setItem("cube.cond", cond); }} catch (e) {{}}
  applyCond();
}});

/* ---- refresh: ask the sidecar to run track.py now ---- */
const rbtn = document.getElementById("refresh"), rmsg = document.getElementById("refresh-msg");
function say(t) {{ rmsg.textContent = t; rmsg.hidden = !t; }}
function stop(msg) {{ say(msg); rbtn.disabled = false; }}
let polls = 0;
async function poll() {{
  // Never leave the button disabled forever: give up after ~5 minutes.
  if (++polls > 100) return stop("still running \u2014 reload to see the result");
  try {{
    const s = await (await fetch("api/refresh")).json();
    if (s.state === "running") {{ say("running\u2026"); setTimeout(poll, 3000); return; }}
    if (s.state === "failed") return stop("job failed \u2014 check the pod logs");
    say("done, reloading\u2026"); location.reload();
  }} catch (e) {{ stop("lost contact with the refresher"); }}
}}
rbtn.onclick = async () => {{
  rbtn.disabled = true; say("starting\u2026");
  try {{
    const res = await fetch("api/refresh", {{method: "POST"}});
    // 409 means someone else already started one -- follow that job rather
    // than reporting a failure the user can do nothing about.
    if (res.status === 409) return poll();
    if (res.status === 429) return stop((await res.json()).error);
    if (!res.ok) return stop("could not start (" + res.status + ")");
    poll();
  }} catch (e) {{ stop("no refresher running"); }}
}};

/* ---- gainers & losers, rankable by $ or % ---- */
const moved = rows.filter(r => r.was !== undefined);
let metric = "delta";
try {{ metric = localStorage.getItem("cube.metric") || "delta"; }} catch (e) {{}}

function moverTable(sign) {{
  // A $0.40 card going to $1.20 is +200% and means nothing, so percent mode
  // keeps the same >=$2 floor the dollar ranking doesn't need.
  const pool = metric === "pct" ? moved.filter(r => r.was * fac(r) >= 2) : moved;
  const mval = r => metric === "pct" ? r.pct : r.delta * fac(r);
  const top = [...pool].sort((a, b) => sign * (mval(b) - mval(a))).slice(0, 12);
  if (!top.length) return '<p class="muted">Not enough history yet.</p>';
  return '<table><thead><tr><th>Card</th><th class="num">Then</th><th class="num">Now</th>' +
    '<th class="num">Change</th><th class="num">%</th></tr></thead><tbody>' +
    top.map(r => {{
      const t = r.delta >= 0 ? "up" : "down";
      const f = fac(r);
      return `<tr>${{cell(r)}}<td class="num">$${{(r.was * f).toFixed(2)}}</td>` +
        `<td class="num">$${{(r.price * f).toFixed(2)}}</td>` +
        `<td class="num money ${{t}}">${{money(r.delta * f)}}</td>` +
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
  document.querySelectorAll("#metric button").forEach(b =>
    b.classList.toggle("on", b.dataset.metric === metric));
}}
const gainH = document.getElementById("gain-h"), lossH = document.getElementById("loss-h");
document.querySelectorAll("#metric button").forEach(b => b.onclick = () => {{
  metric = b.dataset.metric;
  try {{ localStorage.setItem("cube.metric", metric); }} catch (e) {{}}
  drawMovers();
}});
/* ---- every card ---- */
let dir = -1, key = "value";
function draw() {{
  // Sort on the displayed figure: the haircut differs per row, so scaling can
  // reorder the list rather than just relabel it.
  const kv = r => (key === "price" || key === "value") ? r[key] * fac(r) : r[key];
  const sorted = [...rows].sort((a, b) =>
    typeof kv(a) === "string" ? dir * kv(a).localeCompare(kv(b)) : dir * (kv(a) - kv(b)));
  document.querySelector("#all tbody").innerHTML = sorted.map(r =>
    `<tr>${{cell(r)}}<td>${{esc(r.set)}}</td><td class="num">${{r.copies}}</td>` +
    `<td class="num">$${{(r.price * fac(r)).toFixed(2)}}</td>` +
    `<td class="num">$${{(r.value * fac(r)).toFixed(2)}}</td></tr>`).join("");
}}
document.querySelectorAll("#all th").forEach(th => th.onclick = () => {{
  const k = th.dataset.k;
  dir = k === key ? -dir : (k === "name" || k === "set" ? 1 : -1);
  key = k; draw();
}});
applyCond();
</script>
</main></body></html>
"""


if __name__ == "__main__":
    import sys

    data_dir = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
    build(sys.argv[1] if len(sys.argv) > 1 else os.path.join(data_dir, "prices.csv"),
          sys.argv[2] if len(sys.argv) > 2 else os.path.join(data_dir, "dashboard.html"))
