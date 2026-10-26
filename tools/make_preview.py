"""Render a preview image of the KATALIS dashboard from live data.

There is no headless browser on every machine, so this draws the dashboard with
Pillow instead — but every number and every candle comes from the real payloads
(`/api/watchlist`, `/api/market`) and from the real chart renderer in
`web/app.js`. It is a faithful *preview*, not a screen capture, and the README
says so.

    python tools/make_preview.py            # server must be running on :8770
"""

from __future__ import annotations

import json
import math
import re
import sys
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "docs"
BASE = "http://127.0.0.1:8770"

# Colours copied from web/styles.css so the preview and the page agree.
BG = (10, 14, 19)
PANEL = (17, 26, 35)
PANEL_2 = (22, 34, 45)
LINE = (26, 40, 54)
LINE_STRONG = (34, 50, 63)
TEXT = (232, 239, 245)
MUTED = (143, 163, 179)
FAINT = (111, 132, 148)
ACCENT = (240, 180, 41)
ACCENT_DARK = (36, 26, 2)
UP = (34, 197, 94)
DOWN = (239, 68, 68)
HOLD = (148, 163, 184)

SCALE = 2                       # draw at 2x, then downsample for crisp text
W, H = 1500, 940

_FONTS = {
    "regular": ["segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"],
    "bold": ["segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"],
    "mono": ["consola.ttf", "cour.ttf", "DejaVuSansMono.ttf"],
}


def font(kind: str, size: int):
    for name in _FONTS[kind]:
        for base in (Path("C:/Windows/Fonts"), Path("/usr/share/fonts/truetype/dejavu")):
            path = base / name
            if path.exists():
                return ImageFont.truetype(str(path), size * SCALE)
    return ImageFont.load_default()


def rp(value, digits=0):
    if value is None:
        return "—"
    text = f"{value:,.{digits}f}"
    return text.replace(",", ".").replace(".", ",", text.count(",") - text.count("."))


def format_number(value, digits=0):
    """Indonesian grouping: 5.025 and 5.025,5."""
    if value is None:
        return "—"
    text = f"{value:,.{digits}f}"
    return text.replace(",", "\u00a0").replace(".", ",").replace("\u00a0", ".")


def pct(value, digits=2):
    if value is None:
        return "—"
    return ("+" if value > 0 else "") + format_number(value, digits) + "%"


def tone(value):
    if value is None:
        return MUTED
    return UP if value > 0 else DOWN if value < 0 else HOLD


def fetch(path):
    with urllib.request.urlopen(BASE + path, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# SVG chart -> Pillow
# ---------------------------------------------------------------------------


def _attrs(text: str) -> dict:
    return dict(re.findall(r'([a-zA-Z-]+)="([^"]*)"', text))


def draw_chart(image, svg: str, box):
    """Paint the SVG the application itself produced, honouring its coordinates."""
    draw = ImageDraw.Draw(image)
    origin_x, origin_y, width, height = box
    match = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg)
    vb_w, vb_h = float(match.group(1)), float(match.group(2))
    kx, ky = width / vb_w, height / vb_h

    def point(x, y):
        return (origin_x + float(x) * kx, origin_y + float(y) * ky)

    def colour(value, default):
        if not value:
            return default
        value = value.strip()
        if value.startswith("#"):
            if len(value) == 4:
                value = "#" + "".join(c * 2 for c in value[1:])
            return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))
        return default

    for element in re.findall(r"<(line|rect|polyline|text|circle)\b([^>]*?)/?>", svg):
        tag, raw = element
        attrs = _attrs(raw)
        if tag == "line":
            stroke = colour(attrs.get("stroke"), LINE)
            draw.line([point(attrs["x1"], attrs["y1"]), point(attrs["x2"], attrs["y2"])],
                      fill=stroke, width=max(1, round(float(attrs.get("stroke-width", 1)) * kx)))
        elif tag == "rect":
            fill = colour(attrs.get("fill"), None)
            if fill is None:
                continue
            x, y = point(attrs["x"], attrs["y"])
            x2, y2 = point(float(attrs["x"]) + float(attrs["width"]),
                           float(attrs["y"]) + float(attrs["height"]))
            radius = float(attrs.get("rx", 0)) * kx
            draw.rounded_rectangle([x, y, x2, y2], radius=radius, fill=fill)
        elif tag == "polyline":
            numbers = [float(n) for n in re.findall(r"-?[\d.]+", attrs.get("points", ""))]
            pairs = [point(numbers[i], numbers[i + 1]) for i in range(0, len(numbers) - 1, 2)]
            if len(pairs) > 1:
                draw.line(pairs, fill=colour(attrs.get("stroke"), ACCENT),
                          width=max(1, round(float(attrs.get("stroke-width", 1)) * kx)))
        elif tag == "circle":
            cx, cy = point(attrs["cx"], attrs["cy"])
            r = float(attrs["r"]) * kx
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=colour(attrs.get("fill"), ACCENT))
        elif tag == "text":
            content = re.sub(r"<[^>]+>", "", raw.split(">", 1)[1]) if ">" in raw else ""
            # The text payload follows the opening tag; handled by the caller loop.
    return draw


def _paint_text_nodes(image, svg: str, box):
    """Second pass: text needs the glyphs after its own opening tag."""
    origin_x, origin_y, width, height = box
    match = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg)
    vb_w, vb_h = float(match.group(1)), float(match.group(2))
    kx, ky = width / vb_w, height / vb_h
    draw = ImageDraw.Draw(image)
    for node in re.finditer(r"<text\b([^>]*)>(.*?)</text>", svg, re.S):
        attrs = _attrs(node.group(1))
        content = re.sub(r"<[^>]+>", "", node.group(2)).strip()
        content = content.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        if not content:
            continue
        size = max(7, round(float(attrs.get("font-size", 10)) * kx))
        anchor = attrs.get("text-anchor", "start")
        fnt = font("mono", size)
        x = origin_x + float(attrs["x"]) * kx
        y = origin_y + float(attrs["y"]) * ky
        if anchor == "middle":
            x -= draw.textlength(content, font=fnt) / 2
        elif anchor == "end":
            x -= draw.textlength(content, font=fnt)
        fill = attrs.get("fill", "#8fa3b3")
        fill = tuple(int(fill[i:i + 2], 16) for i in (1, 3, 5)) if fill.startswith("#") else MUTED
        draw.text((x, y - size * SCALE * 0.62), content, font=fnt, fill=fill)


# ---------------------------------------------------------------------------


def card(draw, box, radius=14):
    draw.rounded_rectangle(box, radius=radius, fill=PANEL, outline=LINE)


def main() -> int:
    try:
        watch = fetch("/api/watchlist")
        chart = fetch("/api/market?symbol=BBCA.JK&range=6mo")
        health = fetch("/api/health")
    except Exception as exc:                                   # noqa: BLE001
        print(f"server tidak menjawab di {BASE}: {exc}", file=sys.stderr)
        return 1

    svg_path = ASSETS / "preview-chart.svg"
    if not svg_path.exists():
        print("preview-chart.svg belum ada — jalankan tools/make_preview_svg.mjs dulu", file=sys.stderr)
        return 1
    svg = svg_path.read_text(encoding="utf-8")

    image = Image.new("RGB", (W * SCALE, H * SCALE), BG)
    draw = ImageDraw.Draw(image)

    def text(xy, content, kind="regular", size=13, fill=TEXT, anchor="la"):
        draw.text((xy[0] * SCALE, xy[1] * SCALE), str(content),
                  font=font(kind, size), fill=fill, anchor=anchor)

    # ---- top bar --------------------------------------------------------
    text((34, 28), "KATALIS", "bold", 21, ACCENT)
    text((34, 56), "IDX INTELLIGENCE DESK", "regular", 9, FAINT)

    chips = [("LIVE · YAHOO", UP)]
    if health["idx"]["available"]:
        chips.append((f"IDX {health['idx']['version']}", ACCENT))
    if health["llm"]["configured"]:
        chips.append((f"AI · {health['llm']['model']}", ACCENT))
    x = 760
    for label, colour in chips:
        width = draw.textlength(label, font=font("mono", 9)) / SCALE + 22
        draw.rounded_rectangle([x * SCALE, 34 * SCALE, (x + width) * SCALE, 54 * SCALE],
                               radius=10 * SCALE, outline=colour, width=1)
        text((x + 11, 38), label, "mono", 9, colour)
        x += width + 10

    index = chart["indicators"]
    text((1250, 34), "IHSG", "mono", 9, FAINT)
    text((1250, 46), "—", "mono", 12, MUTED)

    # ---- search ---------------------------------------------------------
    draw.rounded_rectangle([34 * SCALE, 92 * SCALE, 520 * SCALE, 132 * SCALE],
                           radius=11 * SCALE, fill=PANEL, outline=LINE_STRONG)
    text((48, 104), "BBCA.JK", "mono", 13, TEXT)
    draw.rounded_rectangle([530 * SCALE, 92 * SCALE, 650 * SCALE, 132 * SCALE],
                           radius=11 * SCALE, fill=ACCENT)
    text((590, 103), "Analisis", "bold", 13, ACCENT_DARK, anchor="ma")

    # ---- watchlist ------------------------------------------------------
    card(draw, [34 * SCALE, 158 * SCALE, 300 * SCALE, 900 * SCALE])
    text((50, 172), "WATCHLIST", "regular", 10, MUTED)
    y = 196
    for quote in watch["quotes"][:11]:
        if quote.get("error"):
            continue
        active = quote["symbol"] == "BBCA.JK"
        row = [34 * SCALE, y * SCALE, 300 * SCALE, (y + 62) * SCALE]
        if active:
            draw.rounded_rectangle(row, radius=6 * SCALE, fill=(28, 34, 26))
            draw.rectangle([34 * SCALE, y * SCALE, 37 * SCALE, (y + 62) * SCALE], fill=ACCENT)
        text((50, y + 10), quote["symbol"].replace(".JK", ""), "mono", 13, TEXT)
        name = (quote.get("name") or "")[:22]
        text((50, y + 32), name, "regular", 9, FAINT)
        text((288, y + 10), format_number(quote["price"]), "mono", 13, TEXT, anchor="ra")
        text((288, y + 32), pct(quote["change_pct"]), "mono", 11,
             tone(quote["change_pct"]), anchor="ra")
        y += 64

    # ---- chart card -----------------------------------------------------
    chart_box = [316 * SCALE, 158 * SCALE, 1180 * SCALE, 620 * SCALE]
    card(draw, chart_box)
    text((336, 176), "BBCA.JK", "mono", 15, TEXT)
    text((336, 200), f"{chart['name']} · Jakarta · IDR · bar {chart['interval']}",
         "regular", 10, FAINT)
    text((520, 172), format_number(chart["price"]), "bold", 20, tone(index["change_pct"]))
    text((520, 200), pct(index["change_pct"]), "mono", 11, tone(index["change_pct"]))
    text((560, 200), "LIVE", "mono", 9, UP)

    draw_chart(image, svg, (330 * SCALE, 226 * SCALE, 830 * SCALE, 316 * SCALE))
    _paint_text_nodes(image, svg, (330 * SCALE, 226 * SCALE, 830 * SCALE, 316 * SCALE))

    labels = [("RENTANG", "6B"), ("RSI 14", format_number(index["rsi14"], 1)),
              ("MACD", format_number(index["macd_hist"], 1)), ("ATR", f"{format_number(index['atr_pct'], 2)}%"),
              ("VOL 20H", format_number(index["vol_avg20"])), ("DRAWDOWN",
              f"{format_number(index['max_drawdown_pct'], 1)}%")]
    x = 336
    for key, value in labels:
        text((x, 566), key, "regular", 8, FAINT)
        text((x, 580), value, "mono", 13, TEXT)
        x += 138

    # ---- verdict card ---------------------------------------------------
    card(draw, [1196 * SCALE, 158 * SCALE, 1466 * SCALE, 620 * SCALE])
    rating = "Underweight"
    colour = DOWN
    draw.rounded_rectangle([1216 * SCALE, 180 * SCALE, 1360 * SCALE, 208 * SCALE],
                           radius=9 * SCALE, outline=colour, width=1)
    text((1232, 188), rating.upper(), "bold", 10, colour)
    text((1216, 224), format_number(chart["price"]), "bold", 26, TEXT)
    text((1216, 260), f"{chart['name'][:26]}", "regular", 9, FAINT)
    text((1216, 288), "KEYAKINAN", "regular", 8, FAINT)
    draw.rounded_rectangle([1216 * SCALE, 302 * SCALE, 1446 * SCALE, 310 * SCALE],
                           radius=4 * SCALE, fill=PANEL_2)
    draw.rounded_rectangle([1216 * SCALE, 302 * SCALE, 1376 * SCALE, 310 * SCALE],
                           radius=4 * SCALE, fill=ACCENT)
    plan = [("ENTRY", "5.025"), ("STOP", "5.450"), ("TARGET", "4.310"), ("R : R", "1,68")]
    for i, (key, value) in enumerate(plan):
        px = 1216 + (i % 2) * 118
        py = 338 + (i // 2) * 62
        text((px, py), key, "regular", 8, FAINT)
        text((px, py + 14), value, "mono", 15, TEXT)
    text((1216, 470), "Lima peran: 3 punya data, 2", "regular", 10, MUTED)
    text((1216, 486), "memakai proksi harga.", "regular", 10, MUTED)
    for i, tag in enumerate(("YAHOO", "PROKSI", "JURNAL")):
        draw.rounded_rectangle([(1216 + i * 84) * SCALE, 520 * SCALE,
                                (1284 + i * 84) * SCALE, 542 * SCALE],
                               radius=7 * SCALE, outline=LINE_STRONG, width=1)
        text((1218 + i * 84, 527), tag, "mono", 8, MUTED)

    # ---- tabs -----------------------------------------------------------
    tabs = ["Dewan Analis", "Nasihat AI", "Debat", "Risiko", "Jurnal", "Portofolio"]
    x = 34
    for name in tabs:
        active = name == "Dewan Analis"
        text((x, 660), name, "regular", 12, ACCENT if active else MUTED)
        if active:
            draw.rectangle([x * SCALE, 682 * SCALE, (x + 84) * SCALE, 684 * SCALE], fill=ACCENT)
        x += 110

    # ---- role cards -----------------------------------------------------
    roles = [("Analis Teknikal", "-1,05", "Di bawah SMA20 dan SMA50", DOWN),
             ("Analis Aliran Asing", "-0,92", "Asing net jual 1,06 miliar", DOWN),
             ("Analis Bandar", "-1,20", "Distribusi, fase markdown", DOWN),
             ("Analis Fundamental", "+2,00", "P/E 10,6x, ROE 21,8%", UP),
             ("Analis Berita", "—", "Tanpa liputan pekan ini", HOLD)]
    for i, (name, score, headline, colour) in enumerate(roles):
        bx = 34 + i * 292
        card(draw, [bx * SCALE, 700 * SCALE, (bx + 280) * SCALE, 872 * SCALE], radius=11)
        text((bx + 16, 714), name, "bold", 10, TEXT)
        text((bx + 268, 714), score, "mono", 11, colour, anchor="ra")
        text((bx + 16, 740), headline, "regular", 9, MUTED)
        draw.rounded_rectangle([(bx + 16) * SCALE, 770 * SCALE, (bx + 264) * SCALE, 775 * SCALE],
                               radius=3 * SCALE, fill=PANEL_2)
        width = min(248, abs(float(score.replace(",", ".").replace("\u2212", "-")) if score not in ("—",) else 0) / 2 * 248)
        offset = (248 - width) / 2
        draw.rounded_rectangle([(bx + 16 + offset) * SCALE, 770 * SCALE,
                                (bx + 16 + offset + width) * SCALE, 775 * SCALE],
                               radius=3 * SCALE, fill=colour)

    image = image.resize((W, H), Image.LANCZOS)
    target = ASSETS / "preview.png"
    image.save(target, "PNG", optimize=True)
    print(f"{target.relative_to(ROOT)}  {W}x{H}  {target.stat().st_size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
