#!/usr/bin/env python3
"""Render a self-hosted contribution activity graph as a static SVG.

Replaces github-readme-activity-graph.vercel.app, which serves HTTP 402 once the
public instance hits its Vercel spend cap. Data comes from the tokenless public
endpoint https://github.com/users/<user>/contributions, so the workflow needs no
secret beyond what Actions already provides.

The SVG is deliberately static: Safari freezes animations inside <img>, and this
image is embedded in the profile README exactly that way.
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.request
from datetime import date, datetime
from html import escape
from xml.sax.saxutils import quoteattr

CONTRIB_URL = "https://github.com/users/{user}/contributions"
UA = "Mozilla/5.0 (compatible; f1lcry-activity-graph/1.0)"

# Palette — matches the rest of the profile README cards (github_dark + #2CA5E0).
BG = "#0D1117"
BORDER = "#21262D"
GRID = "#1B2129"
LINE = "#2CA5E0"
POINT = "#FFFFFF"
TEXT = "#C9D1D9"
MUTED = "#7D8590"
FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"

TD_RE = re.compile(r"<td[^>]*class=\"ContributionCalendar-day\"[^>]*>", re.I)
ATTR_RE = re.compile(r"(data-date|id)=\"([^\"]*)\"")
TOOLTIP_RE = re.compile(r"<tool-tip[^>]*\bfor=\"([^\"]+)\"[^>]*>(.*?)</tool-tip>", re.S)
COUNT_RE = re.compile(r"^\s*(\d+)\s+contribution")


def fetch_days(user: str, year: int | None = None) -> list[tuple[date, int]]:
    """Return [(day, contributions)] ascending, parsed from the public calendar.

    The counts include work in private repositories — anonymously, as GitHub
    itself renders them — but ONLY while "Include private contributions on my
    profile" is enabled in the account settings. If these numbers ever collapse
    to a fraction of reality with no error anywhere, that setting is where to
    look.
    """
    url = CONTRIB_URL.format(user=user)
    if year is not None:
        url += f"?from={year}-01-01&to={year}-12-31"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        html = resp.read().decode("utf-8", "replace")

    counts: dict[str, int] = {}
    for cell_id, label in TOOLTIP_RE.findall(html):
        m = COUNT_RE.match(label)
        counts[cell_id] = int(m.group(1)) if m else 0

    days: list[tuple[date, int]] = []
    for tag in TD_RE.findall(html):
        attrs = dict(ATTR_RE.findall(tag))
        day, cell_id = attrs.get("data-date"), attrs.get("id")
        if not day or cell_id is None:
            continue
        days.append((datetime.strptime(day, "%Y-%m-%d").date(), counts.get(cell_id, 0)))

    if year is not None:
        days = [(d, c) for d, c in days if d.year == year]
    if not days:
        raise SystemExit("no contribution cells parsed — GitHub markup changed")
    days.sort()
    return days


def smooth_path(pts: list[tuple[float, float]], top: float, floor: float) -> str:
    """Catmull-Rom → cubic bezier, with control points clamped inside the plot box."""
    if len(pts) < 2:
        x, y = pts[0]
        return f"M {x:.1f} {y:.1f}"

    def clamp(y: float) -> float:
        return max(top, min(floor, y))

    d = [f"M {pts[0][0]:.1f} {pts[0][1]:.1f}"]
    for i in range(len(pts) - 1):
        p0 = pts[i - 1] if i > 0 else pts[i]
        p1, p2 = pts[i], pts[i + 1]
        p3 = pts[i + 2] if i + 2 < len(pts) else p2
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, clamp(p1[1] + (p2[1] - p0[1]) / 6))
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, clamp(p2[1] - (p3[1] - p1[1]) / 6))
        d.append(
            f"C {c1[0]:.1f} {c1[1]:.1f}, {c2[0]:.1f} {c2[1]:.1f}, {p2[0]:.1f} {p2[1]:.1f}"
        )
    return " ".join(d)


def render(user: str, days: list[tuple[date, int]], window: int) -> str:
    days = days[-window:]
    values = [c for _, c in days]
    total = sum(values)
    peak = max(values)
    scale_max = max(peak, 1)

    W, H = 880, 280
    pad_l, pad_r, pad_t, pad_b = 56, 28, 78, 46
    plot_w = W - pad_l - pad_r
    plot_h = H - pad_t - pad_b
    floor = pad_t + plot_h

    step = plot_w / (len(days) - 1) if len(days) > 1 else 0
    pts = [
        (pad_l + i * step, floor - (v / scale_max) * plot_h)
        for i, (_, v) in enumerate(days)
    ]

    line = smooth_path(pts, pad_t, floor)
    area = f"{line} L {pts[-1][0]:.1f} {floor} L {pts[0][0]:.1f} {floor} Z"

    # Y gridlines at 0 / half / max of the scale.
    grid = []
    for frac in (0.0, 0.5, 1.0):
        y = floor - frac * plot_h
        label = round(scale_max * frac)
        grid.append(
            f'<line x1="{pad_l}" y1="{y:.1f}" x2="{pad_l + plot_w}" y2="{y:.1f}" '
            f'stroke="{GRID}" stroke-width="1" />'
        )
        grid.append(
            f'<text x="{pad_l - 12}" y="{y + 4:.1f}" text-anchor="end" '
            f'font-size="11" fill="{MUTED}">{label}</text>'
        )

    # X labels: roughly six evenly spaced dates, always including the last day.
    every = max(1, (len(days) - 1) // 5 or 1)
    xlabels = []
    for i, (day, _) in enumerate(days):
        if i % every and i != len(days) - 1:
            continue
        xlabels.append(
            f'<text x="{pts[i][0]:.1f}" y="{floor + 24:.1f}" text-anchor="middle" '
            f'font-size="11" fill="{MUTED}">{day.strftime("%b %-d")}</text>'
        )

    dots = "".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{3 if v == peak else 2.2}" '
        f'fill="{POINT if v == peak else LINE}" stroke="{BG}" stroke-width="1.5" />'
        for (x, y), (_, v) in zip(pts, days)
    )

    span = f"{days[0][0].strftime('%b %-d, %Y')} — {days[-1][0].strftime('%b %-d, %Y')}"
    subtitle = f"{total} contributions in the last {len(days)} days · peak {peak} in a day"

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label={quoteattr(f"{user} contribution graph: {subtitle}")}>
  <defs>
    <linearGradient id="fill" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="{LINE}" stop-opacity="0.40" />
      <stop offset="100%" stop-color="{LINE}" stop-opacity="0.02" />
    </linearGradient>
  </defs>
  <style>
    text {{ font-family: {FONT}; }}
  </style>
  <rect x="0.5" y="0.5" width="{W - 1}" height="{H - 1}" rx="10" fill="{BG}" stroke="{BORDER}" />
  <text x="{pad_l - 12}" y="36" font-size="16" font-weight="600" fill="{TEXT}">{escape(user)}'s contribution graph</text>
  <text x="{pad_l - 12}" y="56" font-size="12" fill="{MUTED}">{escape(subtitle)}</text>
  <text x="{W - pad_r}" y="36" text-anchor="end" font-size="12" fill="{MUTED}">{escape(span)}</text>
  {chr(10).join("  " + g for g in grid)}
  <path d="{area}" fill="url(#fill)" />
  <path d="{line}" fill="none" stroke="{LINE}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" />
  {dots}
  {chr(10).join("  " + t for t in xlabels)}
</svg>
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--user", required=True)
    ap.add_argument("--days", type=int, default=31)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    svg = render(args.user, fetch_days(args.user), args.days)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(svg)
    print(f"wrote {args.out} ({len(svg)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
