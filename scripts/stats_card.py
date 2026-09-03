#!/usr/bin/env python3
"""Render the GitHub Stats card as a static SVG, from the public calendar.

Replaces two github-profile-summary-cards tiles that only ever counted PUBLIC
repositories: "Total Commits: 128 / Total PRs: 17" against 2 348 real
contributions, and a commits-per-hour histogram drawn from those same ~130
public commits (i.e. noise). The calendar this reads includes private work —
anonymously, exactly as GitHub reports it on the profile — so no token, no
secret and no private repository name is involved anywhere.

Everything named here is therefore a *contribution* count, never "commits" or
"pull requests": the public data does not carry that breakdown, and labelling
it as if it did is the bug being fixed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date, timedelta
from html import escape

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from activity_graph import BG, BORDER, FONT, GRID, LINE, MUTED, TEXT, UA, fetch_days  # noqa: E402

API = "https://api.github.com"
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def api_json(path: str) -> object:
    headers = {"User-Agent": UA, "Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")  # the workflow's built-in token; public data only
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{API}{path}", headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def all_days(user: str, first_year: int, today: date) -> list[tuple[date, int]]:
    days: list[tuple[date, int]] = []
    for year in range(first_year, today.year + 1):
        days.extend(fetch_days(user, year=year))
    days.sort()
    return [(d, c) for d, c in days if d <= today]


def streaks(days: list[tuple[date, int]], today: date) -> tuple[int, int]:
    """(current, longest) run of consecutive active days.

    A blank *today* does not break the current streak — the day is not over yet;
    that is how GitHub's own streak reads, and it stops the card from flickering
    to 0 every morning.
    """
    longest = run = 0
    for _, count in days:
        run = run + 1 if count else 0
        longest = max(longest, run)

    current = 0
    active = {d for d, c in days if c}
    cursor = today if today in active else today - timedelta(days=1)
    while cursor in active:
        current += 1
        cursor -= timedelta(days=1)
    return current, longest


def collect(user: str) -> dict:
    profile = api_json(f"/users/{user}")
    joined = date.fromisoformat(profile["created_at"][:10])
    today = date.today()
    days = all_days(user, joined.year, today)

    year_days = [(d, c) for d, c in days if d > today - timedelta(days=365)]
    best_day, best = max(days, key=lambda dc: dc[1])
    current, longest = streaks(days, today)

    by_weekday = [0] * 7
    for d, c in year_days:
        by_weekday[d.weekday()] += c

    stars = 0
    page = 1
    while True:
        repos = api_json(f"/users/{user}/repos?per_page=100&page={page}&type=owner")
        if not repos:
            break
        stars += sum(r["stargazers_count"] for r in repos)
        page += 1

    return {
        "user": user,
        "joined": joined,
        "today": today,
        "total": sum(c for _, c in days),
        "year_total": sum(c for _, c in year_days),
        "active_days": sum(1 for _, c in days if c),
        "active_days_year": sum(1 for _, c in year_days if c),
        "current": current,
        "longest": longest,
        "best": best,
        "best_day": best_day,
        "by_weekday": by_weekday,
        "public_repos": profile["public_repos"],
        "stars": stars,
    }


def tile(x: float, y: float, value: str, label: str, sub: str = "") -> str:
    out = [
        f'<text x="{x}" y="{y}" font-size="23" font-weight="600" fill="{TEXT}">{escape(value)}</text>',
        f'<text x="{x}" y="{y + 19}" font-size="11.5" fill="{MUTED}">{escape(label)}</text>',
    ]
    if sub:
        out.append(f'<text x="{x}" y="{y + 35}" font-size="11" fill="{LINE}">{escape(sub)}</text>')
    return "".join(out)


def render(s: dict) -> str:
    W, H = 880, 332
    # Left half: six figures on a 2x3 grid. Right half: weekday distribution.
    col = [56, 268]
    row = [132, 208, 284]

    def fmt(n: int) -> str:
        return f"{n:,}".replace(",", " ")

    tiles = [
        tile(col[0], row[0], fmt(s["total"]), "contributions, all time"),
        tile(col[1], row[0], fmt(s["year_total"]), "in the last 12 months"),
        tile(col[0], row[1], fmt(s["active_days"]), "active days, all time"),
        tile(col[1], row[1], f"{s['active_days_year']}/365", "active days this year"),
        tile(col[0], row[2], f"{s['current']} d", "current streak"),
        tile(col[1], row[2], f"{s['longest']} d", "longest streak"),
    ]

    bx, bw, bh, bbase = 500, 322, 126, 272
    peak = max(s["by_weekday"]) or 1
    slot = bw / 7
    bars = []
    for i, value in enumerate(s["by_weekday"]):
        h = max(2.0, value / peak * bh)
        x = bx + i * slot + slot * 0.22
        w = slot * 0.56
        bars.append(
            f'<rect x="{x:.1f}" y="{bbase - h:.1f}" width="{w:.1f}" height="{h:.1f}" '
            f'rx="3" fill="{LINE}" fill-opacity="{0.95 if value == peak else 0.55}" />'
        )
        bars.append(
            f'<text x="{x + w / 2:.1f}" y="{bbase - h - 7:.1f}" text-anchor="middle" '
            f'font-size="10.5" fill="{MUTED}">{value}</text>'
        )
        bars.append(
            f'<text x="{x + w / 2:.1f}" y="{bbase + 15:.1f}" text-anchor="middle" '
            f'font-size="11" fill="{MUTED}">{WEEKDAYS[i]}</text>'
        )

    meta = (
        f"joined {s['joined'].strftime('%b %-d, %Y')} · {s['public_repos']} public repos"
        f" · {s['stars']} stars"
    )
    note = "public and private repositories — private work is counted anonymously, as GitHub reports it"
    best = f"best day {s['best']} on {s['best_day'].strftime('%b %-d, %Y')}"

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label="GitHub activity for {escape(s['user'])}: {s['total']} contributions all time">
  <style>
    text {{ font-family: {FONT}; }}
  </style>
  <rect x="0.5" y="0.5" width="{W - 1}" height="{H - 1}" rx="10" fill="{BG}" stroke="{BORDER}" />
  <text x="{col[0]}" y="46" font-size="16" font-weight="600" fill="{TEXT}">GitHub activity</text>
  <text x="{col[0]}" y="66" font-size="12" fill="{MUTED}">{escape(note)}</text>
  <text x="{W - 56}" y="46" text-anchor="end" font-size="12" fill="{MUTED}">{escape(meta)}</text>
  <text x="{W - 56}" y="66" text-anchor="end" font-size="12" fill="{LINE}">{escape(best)}</text>
  <line x1="{col[0]}" y1="88" x2="{W - 56}" y2="88" stroke="{GRID}" stroke-width="1" />
  <line x1="{bx - 32}" y1="106" x2="{bx - 32}" y2="{bbase + 4}" stroke="{GRID}" stroke-width="1" />
  <text x="{bx}" y="{bbase + 34}" font-size="11" fill="{MUTED}">contributions by weekday · last 12 months</text>
  {"".join(tiles)}
  {"".join(bars)}
</svg>
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--user", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    svg = render(collect(args.user))
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(svg)
    print(f"wrote {args.out} ({len(svg)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
