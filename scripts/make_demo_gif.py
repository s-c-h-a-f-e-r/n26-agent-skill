#!/usr/bin/env python3
"""Render docs/demo.gif from the synthetic fixture. No bank and no network.

The picture is a short chat, labelled as demo data. Merchants come from
sample/transactions.json, which is made up.
"""
import os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PIL import Image, ImageDraw, ImageFont  # noqa: E402
import eb  # noqa: E402

W, H = 920, 560
BG = (15, 18, 24)
PANEL = (28, 33, 42)
USER = (32, 78, 58)
AGENT = (36, 44, 56)
TEXT = (232, 234, 237)
MUTED = (154, 163, 175)
ACCENT = (125, 211, 168)
WARN = (251, 191, 36)
SANS = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"


def _lines():
    res = eb.detect_recurring(eb.load_transactions([eb.demo_fixture()]), today=eb.date(2026, 10, 31))
    rows = []
    for item in res["items"]:
        if item["status"] != "active" or item["direction"] != "out":
            continue
        rows.append(f"{item['payee'][:28]:<28}  {item['cadence']:<9} {item['typical_amount']:>7.2f}  next {item['next_expected']}")
    lapsed = [item for item in res["items"] if item["status"] != "active"]
    total = f"About {res['monthly_total_out']:.2f} {res['currency']} / month in active recurring charges."
    note = ""
    if lapsed:
        note = f"{lapsed[0]['payee']} looks lapsed (last {lapsed[0]['last']}). This is not every subscription."
    return rows, total, note


def _font(path, size):
    return ImageFont.truetype(path, size)


def _wrap_bubble(draw, text, font, x, y, fill, max_w, anchor="left"):
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pad_x, pad_y = 16, 12
    bw, bh = tw + pad_x * 2, th + pad_y * 2
    if anchor == "right":
        left = x - bw
    else:
        left = x
    draw.rounded_rectangle((left, y, left + bw, y + bh), radius=12, fill=fill)
    draw.text((left + pad_x, y + pad_y - 2), text, font=font, fill=TEXT)
    return bh


def render(state):
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    title = _font(SANS, 22)
    small = _font(SANS, 16)
    mono = _font(MONO, 16)
    d.text((28, 22), "n26-mcp", font=title, fill=ACCENT)
    d.text((150, 26), "DEMO DATA  ·  not a real account", font=small, fill=MUTED)
    y = 78
    if state["user"]:
        y += _wrap_bubble(d, "what recurring charges do I have?", small, W - 28, y, USER, 640, anchor="right") + 18
    if state["intro"]:
        y += _wrap_bubble(d, "From the synthetic fixture. No bank was contacted.", small, 28, y, AGENT, 760) + 16
    if state["rows"]:
        block_h = 18 + 26 * len(state["rows"]) + (28 if state["total"] else 0) + (28 if state["note"] else 0)
        d.rounded_rectangle((28, y, W - 28, y + block_h + 16), radius=12, fill=AGENT)
        ty = y + 14
        for row in state["rows"]:
            d.text((46, ty), row, font=mono, fill=TEXT)
            ty += 26
        if state["total"]:
            d.text((46, ty + 4), state["total"], font=small, fill=ACCENT)
            ty += 28
        if state["note"]:
            d.text((46, ty + 2), state["note"], font=small, fill=WARN)
    return im


def main():
    rows, total, note = _lines()
    frames = []
    # (state, milliseconds)
    frames.append(({"user": False, "intro": False, "rows": [], "total": "", "note": ""}, 2000))
    frames.append(({"user": True, "intro": False, "rows": [], "total": "", "note": ""}, 3000))
    frames.append(({"user": True, "intro": True, "rows": [], "total": "", "note": ""}, 2200))
    shown = []
    for row in rows:
        shown.append(row)
        frames.append(({"user": True, "intro": True, "rows": list(shown), "total": "", "note": ""}, 2200))
    frames.append(({"user": True, "intro": True, "rows": shown, "total": total, "note": ""}, 2800))
    frames.append(({"user": True, "intro": True, "rows": shown, "total": total, "note": note}, 6200))
    images = [render(state).convert("P", palette=Image.ADAPTIVE, colors=64) for state, _ in frames]
    durations = [ms for _, ms in frames]
    out = os.path.join(ROOT, "docs", "demo.gif")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    images[0].save(out, save_all=True, append_images=images[1:], duration=durations, loop=0, optimize=True)
    print(f"{sum(durations) / 1000:.1f}s  {os.path.getsize(out)} bytes  -> {out}")


if __name__ == "__main__":
    main()
