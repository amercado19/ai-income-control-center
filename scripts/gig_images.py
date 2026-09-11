#!/usr/bin/env python3
"""Render the Fiverr gig images described by ``fiverr_kit``.

Gigs without images perform badly, so the concepts in the kit were a blocker dressed up as a
deliverable. This draws them, at Fiverr's exact 1280x769.

Every image makes the same argument, because it is the one that separates these gigs from the
category: **the work is verifiable.** Not "fast delivery", not five stars, not a stock photo of
someone at a laptop - a visible reconciliation, a visible failure path, a visible separation of
assumptions from logic. Those are claims a buyer can hold the delivered work against, which is
also why they are safe to make.

No stock imagery, no borrowed brand marks, no invented review counts or badges. Everything here
is drawn from primitives, so there is nothing to license and nothing that could be mistaken for
someone else's work.

    python3 scripts/gig_images.py [--out portfolio/gig_images] [--gig data_engineering]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover - dev-only script
    print("SKIP: Pillow is not installed. `pip install pillow --break-system-packages`")
    raise SystemExit(0) from None

from aicc import fiverr_kit  # noqa: E402

W, H = 1280, 769  # Fiverr's stated gig-image size

INK = (23, 23, 21)
INK_2 = (82, 81, 78)
MUTED = (137, 135, 129)
PAPER = (250, 249, 246)
CARD = (255, 255, 255)
LINE = (225, 224, 217)
GREEN = (26, 127, 74)
GREEN_BG = (232, 244, 237)
RED = (176, 42, 42)
RED_BG = (252, 235, 235)
AMBER = (168, 112, 20)
ACCENT = (42, 120, 214)

FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")


def font(size: int, *, bold: bool = False, mono: bool = False) -> ImageFont.FreeTypeFont:
    if mono:
        name = "DejaVuSansMono-Bold.ttf" if bold else "DejaVuSansMono.ttf"
    else:
        name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    path = FONT_DIR / name
    if not path.exists():  # pragma: no cover
        return ImageFont.load_default()
    return ImageFont.truetype(str(path), size)


def canvas() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (W, H), PAPER)
    return img, ImageDraw.Draw(img)


def rounded(d: ImageDraw.ImageDraw, box, r: int, fill=None, outline=None, width: int = 1) -> None:
    d.rounded_rectangle(box, radius=r, fill=fill, outline=outline, width=width)


def pill(d: ImageDraw.ImageDraw, xy, text: str, fg, bg, size: int = 20) -> int:
    f = font(size, bold=True)
    x, y = xy
    tw = d.textlength(text, font=f)
    pad_x, h = 16, size + 16
    rounded(d, (x, y, x + tw + pad_x * 2, y + h), (h // 2), fill=bg)
    d.text((x + pad_x, y + h // 2), text, font=f, fill=fg, anchor="lm")
    return int(x + tw + pad_x * 2)


def pill_right(d: ImageDraw.ImageDraw, right_x: int, y: int, text: str, fg, bg, size: int = 20) -> None:
    """Right-aligned pill. Growing leftwards keeps a long label inside the canvas."""
    f = font(size, bold=True)
    tw = d.textlength(text, font=f)
    pad_x, h = 16, size + 16
    x = right_x - (tw + pad_x * 2)
    rounded(d, (x, y, right_x, y + h), (h // 2), fill=bg)
    d.text((x + pad_x, y + h // 2), text, font=f, fill=fg, anchor="lm")


def fit(d: ImageDraw.ImageDraw, text: str, max_w: int, start: int, *, bold: bool = False, mono: bool = False, floor: int = 12):
    """Largest font size at which `text` fits `max_w`.

    Added after two labels ran off the edge of a finished image. Measuring beats eyeballing:
    a clipped word in a gig image is the kind of defect a buyer reads as carelessness.
    """
    size = start
    while size > floor:
        f = font(size, bold=bold, mono=mono)
        if d.textlength(text, font=f) <= max_w:
            return f
        size -= 1
    return font(floor, bold=bold, mono=mono)


def wrap(d: ImageDraw.ImageDraw, text: str, f, max_w: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for word in words:
        trial = f"{cur} {word}".strip()
        if d.textlength(trial, font=f) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def header(d: ImageDraw.ImageDraw, title: str, subtitle: str) -> None:
    f_title = font(50, bold=True)
    lines = wrap(d, title, f_title, W - 160)[:2]
    y = 74
    for line in lines:
        d.text((80, y), line, font=f_title, fill=INK)
        y += 60
    d.text((80, y + 6), subtitle, font=font(25), fill=INK_2)


def footer(d: ImageDraw.ImageDraw, note: str) -> None:
    d.line((80, H - 96, W - 80, H - 96), fill=LINE, width=1)
    d.text((80, H - 64), note, font=font(22, bold=True), fill=INK_2)


# --------------------------------------------------------------------------- the four images


def img_data_engineering(out: Path) -> Path:
    """Three disagreeing sources in, one reconciled table out, with the row counts shown."""
    img, d = canvas()
    header(d, "Your sources do not agree with each other", "One pipeline. Every row accounted for.")

    top, box_h = 300, 66
    sources = [
        ("sales_2024.csv", "Date, Amount, Rep"),
        ("crm_export.xlsx", "created, total, owner"),
        ("api/orders.json", "ts, value, seller"),
    ]
    for i, (name, cols) in enumerate(sources):
        y = top + i * (box_h + 18)
        rounded(d, (80, y, 430, y + box_h), 10, fill=CARD, outline=LINE)
        d.text((100, y + 16), name, font=font(21, bold=True, mono=True), fill=INK)
        d.text((100, y + 42), cols, font=font(17, mono=True), fill=MUTED)

    mid_y = top + (box_h + 18) * 3 // 2 - 9
    d.line((450, mid_y, 560, mid_y), fill=INK_2, width=3)
    d.polygon([(560, mid_y - 9), (578, mid_y), (560, mid_y + 9)], fill=INK_2)

    rounded(d, (598, mid_y - 56, 838, mid_y + 56), 12, fill=CARD, outline=INK_2, width=2)
    d.text((718, mid_y - 26), "NORMALISE", font=font(23, bold=True), fill=INK, anchor="mm")
    d.text((718, mid_y + 4), "+ RECONCILE", font=font(23, bold=True), fill=INK, anchor="mm")
    d.text((718, mid_y + 34), "flag, never drop", font=font(18), fill=MUTED, anchor="mm")

    d.line((838, mid_y, 948, mid_y), fill=INK_2, width=3)
    d.polygon([(948, mid_y - 9), (966, mid_y), (948, mid_y + 9)], fill=INK_2)

    panel_l, panel_r = 986, 1200
    inner = panel_r - panel_l - 48
    rounded(d, (panel_l, top, panel_r, top + (box_h + 18) * 3 - 18), 10, fill=CARD, outline=GREEN, width=2)
    d.text((panel_l + 24, top + 22), "one clean table", font=fit(d, "one clean table", inner, 21, bold=True, mono=True), fill=INK)
    for i, row in enumerate(["date | amount | rep", "2024-01-04 | 1,280", "2024-01-05 |   940"]):
        d.text((panel_l + 24, top + 62 + i * 32), row, font=fit(d, row, inner, 17, mono=True), fill=MUTED)

    pill_right(d, panel_r, top + (box_h + 18) * 3 + 4, "4,812 in - 4,812 out", GREEN, GREEN_BG, size=20)
    footer(d, "Reconciliation report included. If a row was removed, you see which one and why.")
    return save(img, out, "data_engineering")


def img_financial_model(out: Path) -> Path:
    """The tab separation, because that is what makes a model survive six months."""
    img, d = canvas()
    header(d, "A model you can still audit in six months", "Assumptions on their own tab. Every output traces back.")

    tabs = [("Assumptions", True), ("Model", False), ("Summary", False)]
    x, top = 80, 300
    for name, active in tabs:
        f = font(23, bold=True)
        tw = int(d.textlength(name, font=f)) + 52
        rounded(
            d, (x, top, x + tw, top + 52), 8, fill=CARD if active else PAPER, outline=ACCENT if active else LINE, width=2 if active else 1
        )
        d.text((x + tw // 2, top + 26), name, font=f, fill=INK if active else MUTED, anchor="mm")
        if active:
            d.line((x + 14, top + 46, x + tw - 14, top + 46), fill=ACCENT, width=3)
        x += tw + 12

    rounded(d, (80, top + 52, 640, top + 300), 10, fill=CARD, outline=LINE)
    rows = [("Growth rate", "4.5%"), ("Headcount", "12"), ("Unit cost", "$18.40"), ("Churn", "1.2%")]
    for i, (k, v) in enumerate(rows):
        y = top + 84 + i * 52
        d.text((110, y), k, font=font(22), fill=INK_2)
        box = (470, y - 10, 610, y + 30)
        rounded(d, box, 6, fill=GREEN_BG if i == 0 else PAPER, outline=ACCENT if i == 0 else LINE)
        d.text((540, y + 10), v, font=font(22, bold=True, mono=True), fill=INK, anchor="mm")

    src = (612, top + 94)
    for i, ty in enumerate((top + 120, top + 200, top + 280)):
        d.line((src[0], src[1], 700, src[1]), fill=ACCENT, width=2)
        d.line((700, src[1], 700, ty), fill=ACCENT, width=2)
        d.line((700, ty, 760, ty), fill=ACCENT, width=2)
        d.polygon([(760, ty - 7), (776, ty), (760, ty + 7)], fill=ACCENT)
        rounded(d, (786, ty - 24, 1080, ty + 24), 8, fill=CARD, outline=LINE)
        d.text((806, ty), ["Revenue forecast", "Cash flow", "Break-even month"][i], font=font(21), fill=INK, anchor="lm")

    pill(d, (786, top + 300 - 6), "reconciles", GREEN, GREEN_BG, size=20)
    footer(d, "Change one driver. Watch three outputs move. No hunting through nested formulas.")
    return save(img, out, "financial_model")


def img_scheduled_automation(out: Path) -> Path:
    """The loop, plus the branch nobody else draws: what happens when a source is down."""
    img, d = canvas()
    header(d, "The report that builds itself", "On a schedule. And it tells you when it cannot.")

    cy = 430
    nodes = [
        (210, "SCHEDULE", "every Monday 06:00"),
        (500, "PULL", "3 sources"),
        (790, "BUILD", "your format"),
        (1070, "DELIVER", "inbox or sheet"),
    ]
    for x, label, sub in nodes:
        rounded(d, (x - 110, cy - 52, x + 110, cy + 52), 12, fill=CARD, outline=INK_2, width=2)
        d.text((x, cy - 16), label, font=font(24, bold=True), fill=INK, anchor="mm")
        d.text((x, cy + 18), sub, font=font(18), fill=MUTED, anchor="mm")
    for x0, x1 in ((320, 390), (610, 680), (900, 960)):
        d.line((x0, cy, x1, cy), fill=INK_2, width=3)
        d.polygon([(x1, cy - 9), (x1 + 18, cy), (x1, cy + 9)], fill=INK_2)

    # Return edge, drawn under the row so it reads as a cycle rather than a dead end.
    d.line((1070, cy + 52, 1070, cy + 118), fill=MUTED, width=2)
    d.line((1070, cy + 118, 210, cy + 118), fill=MUTED, width=2)
    d.line((210, cy + 118, 210, cy + 52), fill=MUTED, width=2)
    d.polygon([(203, cy + 70), (210, cy + 52), (217, cy + 70)], fill=MUTED)

    # The failure branch. This is the part of the drawing that is actually the selling point.
    d.line((500, cy - 52, 500, cy - 118), fill=RED, width=2)
    d.line((500, cy - 118, 690, cy - 118), fill=RED, width=2)
    d.polygon([(690, cy - 125), (708, cy - 118), (690, cy - 111)], fill=RED)
    rounded(d, (718, cy - 152, 1180, cy - 84), 10, fill=RED_BG, outline=RED)
    d.text((744, cy - 132), "source unavailable -> you get told", font=font(22, bold=True), fill=RED)
    d.text((744, cy - 104), "not an empty report that looks fine", font=font(19), fill=INK_2)

    footer(d, "Built to fail loudly. The silent empty report is the failure that actually costs you.")
    return save(img, out, "scheduled_automation")


def img_spreadsheet_cleanup(out: Path) -> Path:
    """Before and after, with the count guarantee as the whole point."""
    img, d = canvas()
    header(d, "Messy in. Clean out. Nothing lost.", "Row counts reconciled before and after - shown, not promised.")

    top = 300
    rounded(d, (80, top, 600, top + 300), 12, fill=RED_BG, outline=RED)
    d.text((104, top + 22), "BEFORE", font=font(21, bold=True), fill=RED)
    messy = [
        "Date      | amount | Rep name",
        "01/04/24  | 1280   | A. Mercado",
        "2024-01-04| 1,280  | a mercado",
        "4-Jan-24  |  --    | A. Mercado",
        "          | 940    | R. Khan",
    ]
    for i, row in enumerate(messy):
        d.text((104, top + 68 + i * 40), row, font=font(19, mono=True), fill=INK_2)
    d.text((104, top + 268), "3 date formats - 1 duplicate - 1 null", font=font(19, bold=True), fill=RED)

    d.line((624, top + 150, 700, top + 150), fill=INK_2, width=3)
    d.polygon([(700, top + 141), (718, top + 150), (700, top + 159)], fill=INK_2)

    rounded(d, (740, top, 1200, top + 300), 12, fill=GREEN_BG, outline=GREEN)
    d.text((764, top + 22), "AFTER", font=font(21, bold=True), fill=GREEN)
    clean = [
        "date       | amount | rep",
        "2024-01-04 |  1280  | A. Mercado",
        "2024-01-04 |   940  | R. Khan",
        "2024-01-05 |  1140  | A. Mercado",
        "2024-01-06 |   860  | R. Khan",
    ]
    for i, row in enumerate(clean):
        d.text((764, top + 68 + i * 40), row, font=font(19, mono=True), fill=INK_2)

    pill(d, (740, top + 318), "1,000 rows in - 1,000 rows out", GREEN, GREEN_BG, size=21)
    d.text((80, top + 332), "Flagged, never silently deleted.", font=font(21, bold=True), fill=AMBER)
    footer(d, "Silent row loss is the most common defect in this work. You should never take it on trust.")
    return save(img, out, "spreadsheet_cleanup")


def img_pdf_extraction(out: Path) -> Path:
    """The exceptions, shown rather than hidden - which is the opposite of the category."""
    img, d = canvas()
    header(d, "PDFs in. Clean data out. Nothing guessed.", "Unreadable pages are flagged for you, never invented.")

    top = 300
    # Left: a small stack of documents, the top one an invoice with one bad line.
    for dx in (16, 8, 0):
        rounded(d, (80 + dx, top + 16 - dx, 300 + dx, top + 236 - dx), 8, fill=CARD, outline=LINE)
    d.text((104, top + 34), "invoice_0147.pdf", font=font(17, bold=True, mono=True), fill=INK)
    d.line((104, top + 62, 288, top + 62), fill=LINE, width=1)
    # Labels kept short and the amount right-aligned to the card edge: at font 15 the earlier
    # "Widget A   12" ran straight into "1,280.00" with no gap.
    rows = [("Widget A", "1,280.00", False), ("Widget B", "940.00", False), ("Sm?dg?d", "??.??", True)]
    for i, (left, right, flagged) in enumerate(rows):
        y = top + 78 + i * 30
        if flagged:
            rounded(d, (98, y - 5, 292, y + 21), 5, fill=(253, 244, 228))
        d.text((104, y), left, font=font(15, mono=True), fill=AMBER if flagged else MUTED)
        d.text((286, y), right, font=font(15, mono=True), fill=AMBER if flagged else MUTED, anchor="ra")
    d.text((104, top + 186), "total", font=font(15, bold=True, mono=True), fill=INK)
    d.text((286, top + 186), "2,220.00", font=font(15, bold=True, mono=True), fill=INK, anchor="ra")

    d.line((330, top + 120, 420, top + 120), fill=INK_2, width=3)
    d.polygon([(420, top + 111), (438, top + 120), (420, top + 129)], fill=INK_2)

    # Right: the extracted grid, with the flagged row carried through rather than dropped.
    gx, gr = 458, 1200
    rounded(d, (gx, top, gr, top + 250), 10, fill=CARD, outline=GREEN, width=2)
    d.text((gx + 24, top + 20), "extracted.csv", font=font(19, bold=True, mono=True), fill=INK)
    head = "doc         | item      | qty | amount"
    d.text((gx + 24, top + 56), head, font=fit(d, head, gr - gx - 48, 17, mono=True), fill=INK_2)
    grid = [
        ("0147        | Widget A  |  12 | 1280.00", False),
        ("0147        | Widget B  |   4 |  940.00", False),
        ("0147        | NEEDS YOU |   ? |       ?", True),
    ]
    for i, (row, flagged) in enumerate(grid):
        y = top + 92 + i * 34
        if flagged:
            rounded(d, (gx + 14, y - 7, gr - 14, y + 23), 6, fill=(253, 244, 228), outline=AMBER)
        d.text((gx + 24, y), row, font=fit(d, row, gr - gx - 48, 17, mono=True), fill=AMBER if flagged else MUTED)
    d.text((gx + 24, top + 208), "page 3 could not be read confidently", font=font(16), fill=AMBER)

    pill_right(d, gr, top + 268, "148 of 150 parsed - 2 flagged for you", GREEN, GREEN_BG, size=20)
    footer(d, "A plausible wrong number costs far more than a blank you know about.")
    return save(img, out, "pdf_extraction")


RENDERERS = {
    "data_engineering": img_data_engineering,
    "financial_model": img_financial_model,
    "scheduled_automation": img_scheduled_automation,
    "spreadsheet_cleanup": img_spreadsheet_cleanup,
    "pdf_extraction": img_pdf_extraction,
}


MARGIN = 20


def assert_nothing_clipped(img: Image.Image, key: str) -> None:
    """Refuse to save an image with ink in the outer margin.

    Two labels ran off the right edge of a finished image before this existed, and neither was
    obvious until the PNG was opened. A gig image is a sales asset; a clipped word in one reads
    as carelessness about detail, which is the exact opposite of what these gigs claim.
    """
    px = img.load()
    w, h = img.size
    bad: list[tuple[int, int]] = []
    for x in range(w):
        for y in list(range(MARGIN)) + list(range(h - MARGIN, h)):
            if px[x, y] != PAPER:
                bad.append((x, y))
    for y in range(h):
        for x in list(range(MARGIN)) + list(range(w - MARGIN, w)):
            if px[x, y] != PAPER:
                bad.append((x, y))
    if bad:
        xs = [p[0] for p in bad]
        ys = [p[1] for p in bad]
        raise AssertionError(f"{key}: {len(bad)} pixels of content in the {MARGIN}px margin (x {min(xs)}-{max(xs)}, y {min(ys)}-{max(ys)})")


def save(img: Image.Image, out: Path, key: str) -> Path:
    assert_nothing_clipped(img, key)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{key}.png"
    img.save(path, "PNG", optimize=True)
    return path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="portfolio/gig_images")
    ap.add_argument("--gig", default=None, help="Render one gig only")
    args = ap.parse_args()

    known = {g.key for g in fiverr_kit.all_gigs()}
    missing = known - set(RENDERERS)
    if missing:
        print(f"WARNING: no image renderer for {sorted(missing)} - those gigs would publish without one.")

    out = Path(args.out)
    targets = [args.gig] if args.gig else list(RENDERERS)
    for key in targets:
        renderer = RENDERERS.get(key)
        if renderer is None:
            print(f"No renderer for {key!r}. Known: {', '.join(RENDERERS)}")
            return 2
        path = renderer(out)
        with Image.open(path) as im:
            assert im.size == (W, H), f"{path} is {im.size}, Fiverr expects {(W, H)}"
        print(f"  {path}  {im.size[0]}x{im.size[1]}  {path.stat().st_size // 1024} KB")
    print(f"\n{len(targets)} image(s) written to {out}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
