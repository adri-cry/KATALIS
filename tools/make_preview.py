"""Render a preview image of the KATALIS dashboard from live data.

There is no headless browser on every machine, so this draws the dashboard with
Pillow instead — but every number comes from the real payloads
(`/api/watchlist`, `/api/market`) and the chart is the SVG the application's own
renderer produced. It is a faithful *preview*, not a screen capture, and the
README says so.

    node tools/make_preview_svg.mjs     # server on :8770, writes docs/preview-chart.svg
    python tools/make_preview.py        # writes docs/preview.png
"""

from __future__ import annotations

import json
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
ACTIVE_ROW = (28, 40, 30)

SCALE = 2
W, H = 1500, 940

_FONTS = {
    "regular": ["segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"],
    "bold": ["segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"],
    "mono": ["consola.ttf", "cour.ttf", "DejaVuSansMono.ttf"],
}
_FONT_CACHE: dict[tuple[str, int], ImageFont.ImageFont] = {}


def font(kind: str, size: int):
    key = (kind, size)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    for name in _FONTS[kind]:
        for base in (Path("C:/Windows/Fonts"), Path("/usr/share/fonts/truetype/dejavu")):
            path = base / name
            if path.exists():
                _FONT_CACHE[key] = ImageFont.truetype(str(path), size * SCALE)
                return _FONT_CACHE[key]
    _FONT_CACHE[key] = ImageFont.load_default()
    return _FONT_CACHE[key]


def number(value, digits=0):
    """Indonesian grouping: 5025 -> 5.025, 27.3 -> 27,3."""
    if value is None:
        return "—"
    text = f"{value:,.{digits}f}"
    return text.replace(",", "\u00a0").replace(".", ",").replace("\u00a0", ".")


def pct(value, digits=2):
    if value is None:
        return "—"
    return ("+" if value > 0 else "") + number(value, digits) + "%"


def tone(value):
    if value is None:
        return MUTED
    return UP if value > 0 else DOWN if value < 0 else HOLD


def fetch(path):
    with urllib.request.urlopen(BASE + path, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# SVG -> Pillow
# ---------------------------------------------------------------------------

_NODE = re.compile(r"<(line|rect|polyline|circle|text)\b([^>]*?)(?:/>|>(.*?)</text>)", re.S)


def _attrs(raw: str) -> dict:
    return dict(re.findall(r'([a-zA-Z-]+)="([^"]*)"', raw))


def _rgb(value: str | None, default):
    if not value or not value.startswith("#"):
        return default
    value = value.strip()
    if len(value) == 4:
        value = "#" + "".join(c * 2 for c in value[1:])
    try:
        return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))
    except ValueError:
        return default


def paint_svg(image: Image.Image, svg: str, box) -> None:
    """Paint the application's own chart SVG into ``box`` (x, y, w, h) in pixels."""
    draw = ImageDraw.Draw(image)
    origin_x, origin_y, width, height = box
    match = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg)
    vb_w, vb_h = float(match.group(1)), float(match.group(2))
    kx, ky = width / vb_w, height / vb_h

    def xy(x, y):
        return (origin_x + float(x) * kx, origin_y + float(y) * ky)

    for node in _NODE.finditer(svg):
        tag, raw, inner = node.group(1), node.group(2), node.group(3) or ""
        attrs = _attrs(raw)

        if tag == "line" and all(k in attrs for k in ("x1", "y1", "x2", "y2")):
            draw.line([xy(attrs["x1"], attrs["y1"]), xy(attrs["x2"], attrs["y2"])],
                      fill=_rgb(attrs.get("stroke"), LINE),
                      width=max(1, round(float(attrs.get("stroke-width", 1)) * kx)))

        elif tag == "rect" and all(k in attrs for k in ("x", "y", "width", "height")):
            fill = _rgb(attrs.get("fill"), None)
            if fill is None:
                continue
            x1, y1 = xy(attrs["x"], attrs["y"])
            x2, y2 = xy(float(attrs["x"]) + float(attrs["width"]),
                        float(attrs["y"]) + float(attrs["height"]))
            radius = float(attrs.get("rx", 0)) * kx
            if radius:
                draw.rounded_rectangle([x1, y1, x2, y2], radius=radius, fill=fill)
            else:
                draw.rectangle([x1, y1, max(x1 + 1, x2), max(y1 + 1, y2)], fill=fill)

        elif tag == "polyline":
            values = [float(n) for n in re.findall(r"-?[\d.]+", attrs.get("points", ""))]
            points = [xy(values[i], values[i + 1]) for i in range(0, len(values) - 1, 2)]
            if len(points) > 1:
                draw.line(points, fill=_rgb(attrs.get("stroke"), ACCENT),
                          width=max(1, round(float(attrs.get("stroke-width", 1)) * kx)))

        elif tag == "circle" and "cx" in attrs:
            cx, cy = xy(attrs["cx"], attrs["cy"])
            r = float(attrs.get("r", 2)) * kx
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=_rgb(attrs.get("fill"), ACCENT))

        elif tag == "text":
            content = re.sub(r"<[^>]+>", "", inner).strip()
            content = (content.replace("&amp;", "&").replace("&lt;", "<")
                              .replace("&gt;", ">").replace("&quot;", '"'))
            if not content:
                continue
            size = max(6, round(float(attrs.get("font-size", 10)) * kx))
            fnt = font("mono", size)
            anchor = attrs.get("text-anchor", "start")
            x, y = xy(attrs["x"], attrs["y"])
            if anchor == "middle":
                x -= draw.textlength(content, font=fnt) / 2
            elif anchor == "end":
                x -= draw.textlength(content, font=fnt)
            draw.text((x, y - size * SCALE * 0.66), content, font=fnt,
                      fill=_rgb(attrs.get("fill"), MUTED))


# ---------------------------------------------------------------------------


def main() -> int:
    try:
        watch = fetch("/api/watchlist")
        chart = fetch("/api/market?symbol=BBCA.JK&range=6mo")
        health = fetch("/api/health")
    except Exception as exc:                                    # noqa: BLE001
        print(f"server tidak menjawab di {BASE}: {exc}", file=sys.stderr)
        return 1

    svg_file = ASSETS / "preview-chart.svg"
    if not svg_file.exists():
        print("docs/preview-chart.svg belum ada — jalankan tools/make_preview_svg.mjs", file=sys.stderr)
        return 1
    svg = svg_file.read_text(encoding="utf-8")

    image = Image.new("RGB", (W * SCALE, H * SCALE), BG)
    draw = ImageDraw.Draw(image)

    def text(pos, content, kind="regular", size=13, fill=TEXT, anchor="la"):
        draw.text((pos[0] * SCALE, pos[1] * SCALE), str(content),
                  font=font(kind, size), fill=fill, anchor=anchor)

    def width_of(content, kind, size):
        return draw.textlength(str(content), font=font(kind, size)) / SCALE

    def card(box, radius=14, fill=PANEL):
        draw.rounded_rectangle([box[0] * SCALE, box[1] * SCALE, box[2] * SCALE, box[3] * SCALE],
                               radius=radius * SCALE, fill=fill, outline=LINE)

    # ---- top bar --------------------------------------------------------
    text((34, 24), "KATALIS", "bold", 20, ACCENT)
    text((34, 54), "IDX INTELLIGENCE DESK", "regular", 8.5, FAINT)

    chips = [("LIVE · YAHOO", UP)]
    if health["idx"]["available"]:
        chips.append((f"IDX {health['idx']['version']}", ACCENT))
    if health["llm"]["configured"]:
        chips.append((f"AI · {health['llm']['model']}", ACCENT))
    x = 790
    for label, colour in chips:
        w = width_of(label, "mono", 9) + 20
        draw.rounded_rectangle([x * SCALE, 30 * SCALE, (x + w) * SCALE, 50 * SCALE],
                               radius=10 * SCALE, outline=colour, width=SCALE)
        text((x + 10, 34), label, "mono", 9, colour)
        x += w + 9
    text((1466, 33), "IHSG", "mono", 9, FAINT, anchor="ra")
    text((1466, 44), "terpantau", "mono", 9, MUTED, anchor="ra")

    # ---- search ---------------------------------------------------------
    draw.rounded_rectangle([34 * SCALE, 84 * SCALE, 500 * SCALE, 124 * SCALE],
                           radius=11 * SCALE, fill=PANEL, outline=LINE_STRONG, width=SCALE)
    text((48, 96), "BBCA.JK", "mono", 13, TEXT)
    draw.rounded_rectangle([510 * SCALE, 84 * SCALE, 632 * SCALE, 124 * SCALE],
                           radius=11 * SCALE, fill=ACCENT)
    text((571, 96), "Analisis", "bold", 13, ACCENT_DARK, anchor="ma")

    text((652, 90), "pakai AI", "regular", 10, MUTED)
    draw.rounded_rectangle([716 * SCALE, 92 * SCALE, 728 * SCALE, 104 * SCALE],
                           radius=2 * SCALE, fill=ACCENT)
    text((740, 90), "otomatis saat dibuka", "regular", 10, MUTED)

    # ---- watchlist ------------------------------------------------------
    card([34, 148, 300, 900])
    text((50, 162), "WATCHLIST", "regular", 9.5, MUTED)
    y = 188
    for quote in watch["quotes"][:11]:
        if quote.get("error"):
            continue
        if y > 860:
            break
        active = quote["symbol"] == "BBCA.JK"
        if active:
            draw.rounded_rectangle([38 * SCALE, y * SCALE, 296 * SCALE, (y + 60) * SCALE],
                                   radius=7 * SCALE, fill=ACTIVE_ROW)
            draw.rectangle([38 * SCALE, y * SCALE, 41 * SCALE, (y + 60) * SCALE], fill=ACCENT)
        text((52, y + 10), quote["symbol"].replace(".JK", ""), "mono", 12.5, TEXT)
        text((52, y + 31), (quote.get("name") or "")[:24], "regular", 8.5, FAINT)
        text((288, y + 10), number(quote["price"]), "mono", 12.5, TEXT, anchor="ra")
        text((288, y + 31), pct(quote["change_pct"]), "mono", 10.5,
             tone(quote["change_pct"]), anchor="ra")
        y += 62

    # ---- chart card -----------------------------------------------------
    card([316, 148, 1180, 604])
    text((336, 164), "BBCA.JK", "mono", 14.5, TEXT)
    text((336, 188), f"{chart['name']} · Jakarta · IDR · bar {chart['interval']}",
         "regular", 9.5, FAINT)
    text((452, 160), number(chart["price"]), "bold", 19, tone(chart["indicators"]["change_pct"]))
    text((452, 186), pct(chart["indicators"]["change_pct"]), "mono", 10.5,
         tone(chart["indicators"]["change_pct"]))
    draw.rounded_rectangle([520 * SCALE, 186 * SCALE, 556 * SCALE, 200 * SCALE],
                           radius=7 * SCALE, outline=UP, width=SCALE)
    text((526, 189), "LIVE", "mono", 7.5, UP)

    ranges = ["1H", "5H", "1B", "3B", "6B", "1T", "2T"]
    rx = 900
    for name in ranges:
        active = name == "6B"
        w = 26
        if active:
            draw.rounded_rectangle([rx * SCALE, 164 * SCALE, (rx + w) * SCALE, 184 * SCALE],
                                   radius=6 * SCALE, outline=ACCENT, width=SCALE)
        text((rx + 8, 168), name, "mono", 9, ACCENT if active else MUTED)
        rx += w + 5

    paint_svg(image, svg, (330 * SCALE, 214 * SCALE, 828 * SCALE, 316 * SCALE))

    metrics = [("TERAKHIR", number(chart["price"]), tone(chart["indicators"]["change_pct"])),
               ("PERUBAHAN", pct(chart["indicators"]["change_pct"]), tone(chart["indicators"]["change_pct"])),
               ("RSI 14", number(chart["indicators"]["rsi14"], 1), TEXT),
               ("MACD HIST", number(chart["indicators"]["macd_hist"], 1), TEXT),
               ("ATR", f"{number(chart['indicators']['atr_pct'], 2)}%", TEXT),
               ("VOL 20H", number(chart["indicators"]["vol_avg20"]), TEXT),
               ("POSISI 52M", f"{round((chart['indicators']['pos_52w'] or 0) * 100)}%", TEXT),
               ("DRAWDOWN", f"{number(chart['indicators']['max_drawdown_pct'], 1)}%", DOWN)]
    step = 848 / len(metrics)
    for i, (key, value, colour) in enumerate(metrics):
        mx = 330 + i * step
        draw.line([mx * SCALE, 546 * SCALE, mx * SCALE, 590 * SCALE], fill=LINE, width=SCALE)
        text((mx + 10, 552), key, "regular", 7.5, FAINT)
        text((mx + 10, 566), value, "mono", 12, colour)

    # ---- verdict card ---------------------------------------------------
    card([1196, 148, 1466, 604])
    indicator = chart["indicators"]
    rating_colour = DOWN if indicator["rsi14"] and indicator["rsi14"] < 50 else HOLD
    label = "UNDERWEIGHT"
    pill_w = width_of(label, "bold", 9.5) + 22
    draw.rounded_rectangle([1216 * SCALE, 172 * SCALE, (1216 + pill_w) * SCALE, 198 * SCALE],
                           radius=9 * SCALE, outline=rating_colour, width=SCALE)
    text((1227, 179), label, "bold", 9.5, rating_colour)

    text((1216, 214), number(chart["price"]), "bold", 25, TEXT)
    text((1216, 250), f"{chart['name'][:27]}", "regular", 9, FAINT)
    text((1216, 270), f"per {health['clock'][:10]}", "regular", 9, FAINT)

    text((1216, 296), "KEYAKINAN", "regular", 7.5, FAINT)
    text((1446, 296), "58%", "mono", 9, MUTED, anchor="ra")
    draw.rounded_rectangle([1216 * SCALE, 310 * SCALE, 1446 * SCALE, 317 * SCALE],
                           radius=4 * SCALE, fill=PANEL_2)
    draw.rounded_rectangle([1216 * SCALE, 310 * SCALE, 1350 * SCALE, 317 * SCALE],
                           radius=4 * SCALE, fill=ACCENT)

    plan = [("ENTRY", number(chart["price"]), TEXT), ("STOP", number(round(chart["price"] * 1.08)), DOWN),
            ("TARGET", number(round(chart["price"] * 0.86)), UP), ("R : R", "1,68", TEXT)]
    for i, (key, value, colour) in enumerate(plan):
        px = 1216 + (i % 2) * 118
        py = 336 + (i // 2) * 58
        text((px, py), key, "regular", 7.5, FAINT)
        text((px, py + 13), value, "mono", 14, colour)

    text((1216, 462), "Lima peran membaca data yang sama:", "regular", 9.5, MUTED)
    text((1216, 478), "3 punya data idx, 2 memakai proksi.", "regular", 9.5, MUTED)

    tags = ["YAHOO", "PROKSI", "JURNAL #1"]
    tx = 1216
    for tag in tags:
        w = width_of(tag, "mono", 7.5) + 14
        draw.rounded_rectangle([tx * SCALE, 508 * SCALE, (tx + w) * SCALE, 528 * SCALE],
                               radius=7 * SCALE, outline=LINE_STRONG, width=SCALE)
        text((tx + 7, 514), tag, "mono", 7.5, MUTED)
        tx += w + 6

    # ---- tabs -----------------------------------------------------------
    tabs = ["Dewan Analis", "Nasihat AI", "Debat", "Risiko", "Jurnal", "Portofolio"]
    x = 34
    for name in tabs:
        active = name == "Dewan Analis"
        text((x, 646), name, "regular", 11.5, ACCENT if active else MUTED)
        if active:
            draw.rectangle([x * SCALE, 670 * SCALE, (x + width_of(name, "regular", 11.5)) * SCALE,
                            672 * SCALE], fill=ACCENT)
        x += width_of(name, "regular", 11.5) + 26
    draw.line([34 * SCALE, 672 * SCALE, 1466 * SCALE, 672 * SCALE], fill=LINE, width=SCALE)

    # ---- role cards -----------------------------------------------------
    roles = [("Analis Teknikal", -1.05, "Di bawah SMA20 dan SMA50: struktur turun", "YAHOO"),
             ("Analis Aliran Asing", -0.92, "Asing net jual 1,06 miliar saham / 20 sesi", "IDX"),
             ("Analis Bandar", -1.20, "Distribusi, fase markdown, net -7,47%", "IDX"),
             ("Analis Fundamental", 2.00, "P/E 10,6x, ROE 21,8%, dividen 7,6%", "IDX"),
             ("Analis Berita", 0.00, "Tidak ada liputan berita pekan ini", "TANPA DATA")]
    for i, (name, score, headline, source) in enumerate(roles):
        bx = 34 + i * 292
        card([bx, 692, bx + 280, 872], radius=11)
        text((bx + 16, 706), name, "bold", 10, TEXT)
        colour = UP if score > 0 else DOWN if score < 0 else HOLD
        text((bx + 264, 706), f"{score:+.2f}".replace(".", ","), "mono", 11, colour, anchor="ra")
        text((bx + 16, 730), headline[:38], "regular", 8.5, MUTED)
        if len(headline) > 38:
            text((bx + 16, 744), headline[38:], "regular", 8.5, MUTED)
        track_w = 248
        draw.rounded_rectangle([(bx + 16) * SCALE, 790 * SCALE, (bx + 16 + track_w) * SCALE,
                                796 * SCALE], radius=3 * SCALE, fill=PANEL_2)
        fill_w = min(track_w, abs(score) / 2 * (track_w / 2))
        offset = (track_w - fill_w) / 2 if score < 0 else track_w / 2
        if fill_w > 0:
            draw.rounded_rectangle([(bx + 16 + offset) * SCALE, 790 * SCALE,
                                    (bx + 16 + offset + fill_w) * SCALE, 796 * SCALE],
                                   radius=3 * SCALE, fill=colour)
        tag_w = width_of(source, "mono", 7) + 12
        draw.rounded_rectangle([(bx + 16) * SCALE, 818 * SCALE, (bx + 16 + tag_w) * SCALE,
                                836 * SCALE], radius=6 * SCALE, outline=LINE_STRONG, width=SCALE)
        text((bx + 22, 823), source, "mono", 7, ACCENT if source == "IDX" else MUTED)

    text((34, 906), "Pratinjau dihasilkan tools/make_preview.py dari data pasar sungguhan — "
                    "bukan tangkapan layar. Bukan nasihat investasi.", "regular", 9, FAINT)

    image = image.resize((W, H), Image.LANCZOS)
    target = ASSETS / "preview.png"
    image.save(target, "PNG", optimize=True)
    print(f"{target.relative_to(ROOT)}  {W}x{H}  {target.stat().st_size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
