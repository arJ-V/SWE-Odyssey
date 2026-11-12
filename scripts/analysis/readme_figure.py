#!/usr/bin/env python3
"""Draw docs/results.svg: every system's warm ÷ cold ratio on the pilot sequence, with intervals.

    python3 scripts/analysis/readme_figure.py   # reads jobs/poc-httrack/summary.json (from poc_ratios.py)

One row per system, two panels: steps per ticket and new work per ticket.
Rows are sorted by the steps ratio. Systems whose runs are not finished are left out.
"""
import json, math
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
S = json.load(open(REPO / "jobs/poc-httrack/summary.json"))
ROWS = [("atlas: Claude Code + Opus 5.5", "Claude Code · Opus 5.5"), ("atlas: Claude Code + Sonnet 5.5", "Claude Code · Sonnet 5.5"),
        ("atlas: Claude Code + Haiku 5.5", "Claude Code · Haiku 5.5"), ("atlas: Codex + GPT-6.1 Sol", "Codex · GPT-6.1 Sol"),
        ("atlas: Codex + GPT-6.1 Terra", "Codex · GPT-6.1 Terra"), ("atlas: Codex + GPT-6 Luna", "Codex · GPT-6 Luna"),
        ("atlas: DeepSeek Harness + Kimi K3", "DeepSeek Harness · Kimi K3"), ("atlas: DeepSeek Harness + Kimi K3.1", "DeepSeek Harness · Kimi K3.1"),
        ("atlas: DeepSeek Harness + Qwen3 Coder Next", "DeepSeek Harness · Qwen3 Coder Next"),
        ("atlas: DeepSeek Harness + DeepSeek V3.2", "DeepSeek Harness · DeepSeek V3.2 (reasoning off)")]
COLOR = {"Claude Code": "#2F6690", "Codex": "#B8501F", "DeepSeek Harness": "#2E8B57"}

rows = []
for key, label in ROWS:
    v = S.get(key)
    if not v or not v.get("n_llm_calls", {}).get("pos2plus"):
        continue
    rows.append({"label": label, "harness": label.split(" · ")[0], "seq": v["sequences_used"],
                 "steps": (v["n_llm_calls"]["pos2plus"], *v["n_llm_calls"]["ci95"]),
                 "work": (v["it_delta_net"]["pos2plus"], *v["it_delta_net"]["ci95"]),
                 "solved": f"{v['warm_solved']}/{v['warm_ticket_runs']}"})
rows.sort(key=lambda r: r["steps"][0])

W = 1000; RH = 34; TOP = 96; LEFT = 300; PW = 300; GAP = 60; BOT = 70
H = TOP + RH * len(rows) + BOT
F = "font-family='-apple-system,Segoe UI,Helvetica,Arial,sans-serif'"
lo, hi = 0.25, 1.4
def X(x0, v): return x0 + (math.log(min(max(v, lo), hi)) - math.log(lo)) / (math.log(hi) - math.log(lo)) * PW

svg = [f"<svg xmlns='http://www.w3.org/2000/svg' width='{W}' height='{H}' viewBox='0 0 {W} {H}' {F}>",
       f"<rect width='{W}' height='{H}' rx='12' fill='#FBFBF8'/>",
       "<text x='28' y='36' font-size='18' font-weight='600' fill='#14213D'>Warm ÷ cold per ticket on the pilot sequence</text>",
       "<text x='28' y='58' font-size='12' fill='#6A7179'>Below 1: the agent needed less for the same ticket once it had worked the earlier ones. Bars: 95% intervals from resampling sequences. Log scale.</text>"]
panels = [("steps per ticket", "steps", LEFT), ("new work per ticket", "work", LEFT + PW + GAP)]
for title, _, x0 in panels:
    svg.append(f"<text x='{x0}' y='{TOP-14}' font-size='13' font-weight='600' fill='#14213D'>{title}</text>")
    for t in (0.25, 0.5, 1.0):
        x = X(x0, t)
        svg.append(f"<line x1='{x:.1f}' y1='{TOP-4}' x2='{x:.1f}' y2='{TOP + RH*len(rows)}' stroke='{'#9AA0A8' if t == 1 else '#E3E1D9'}' stroke-dasharray='{'5,4' if t == 1 else '0'}'/>")
        svg.append(f"<text x='{x:.1f}' y='{TOP + RH*len(rows) + 18}' font-size='11' text-anchor='middle' fill='#6A7179'>{t:g}</text>")
for i, r in enumerate(rows):
    y = TOP + RH * i + RH / 2
    c = COLOR[r["harness"]]
    svg.append(f"<text x='28' y='{y+4:.1f}' font-size='13' fill='#14213D'>{r['label']}</text>")
    svg.append(f"<text x='{LEFT-12}' y='{y+4:.1f}' font-size='11' text-anchor='end' fill='#6A7179'>{r['seq']} seq · {r['solved']}</text>")
    for _, fld, x0 in panels:
        m, a, b = r[fld]
        svg.append(f"<line x1='{X(x0,a):.1f}' y1='{y:.1f}' x2='{X(x0,b):.1f}' y2='{y:.1f}' stroke='{c}' stroke-width='3' stroke-opacity='0.45' stroke-linecap='round'/>")
        svg.append(f"<circle cx='{X(x0,m):.1f}' cy='{y:.1f}' r='5' fill='{c}' stroke='#FBFBF8' stroke-width='1.5'/>")
        svg.append(f"<text x='{x0+PW+10}' y='{y+4:.1f}' font-size='12' fill='#14213D'>{m:.2f}</text>")
leg = "  ".join(f"<tspan fill='{c}'>●</tspan> {h}" for h, c in COLOR.items())
svg.append(f"<text x='28' y='{H-30}' font-size='12' fill='#14213D'>{leg}</text>")
svg.append(f"<text x='28' y='{H-12}' font-size='11' fill='#6A7179'>A row is one model inside its vendor's agent at default settings: a system, not a model. Sequences run once; two cold runs per ticket. seq = warm sequences; n/m = warm tickets solved.</text>")
svg.append("</svg>")
text = "\n".join(svg)
# A content-hashed name, so GitHub's image cache never serves a stale copy of the figure.
import hashlib, re as _re
name = f"docs/results-{hashlib.sha256(text.encode()).hexdigest()[:8]}.svg"
for old in REPO.glob("docs/results*.svg"):
    old.unlink()
(REPO / name).write_text(text)
readme = REPO / "README.md"
readme.write_text(_re.sub(r"\(docs/results[^)]*\.svg\)", f"({name})", readme.read_text()))
print(name + ":", len(rows), "systems")
