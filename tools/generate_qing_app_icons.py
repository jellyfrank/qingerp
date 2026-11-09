#!/usr/bin/env python3
"""Generate the three flat QingERP application icons at 256 px."""
from pathlib import Path
from PIL import Image, ImageDraw

SCALE = 4
SIZE = 256
ROOT = Path(__file__).resolve().parents[1]


def p(value):
    return round(value * SCALE)


def points(coords):
    return [(p(x), p(y)) for x, y in coords]


def rect_box(coords):
    return tuple(p(value) for value in coords)


def base(background):
    image = Image.new("RGBA", (p(SIZE), p(SIZE)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(rect_box((8, 8, 248, 248)), radius=p(46), fill=background)
    return image, draw


def save(image, module):
    target = ROOT / module / "static" / "description" / "icon.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    image.resize((SIZE, SIZE), Image.Resampling.LANCZOS).save(target, optimize=True)


def rental():
    image, draw = base("#62517F")
    # A sturdy equipment crate plus a clock communicates time-based rental.
    draw.polygon(points([(60, 97), (120, 65), (180, 97), (120, 129)]), fill="#FFFFFF")
    draw.polygon(points([(60, 97), (120, 129), (120, 190), (60, 157)]), fill="#DED7ED")
    draw.polygon(points([(120, 129), (180, 97), (180, 157), (120, 190)]), fill="#BBB0D5")
    draw.line(points([(120, 129), (120, 190)]), fill="#62517F", width=p(5))
    draw.line(points([(89, 81), (149, 113), (149, 137)]), fill="#E9BD71", width=p(10), joint="curve")
    draw.ellipse(rect_box((148, 148, 220, 220)), fill="#F4B95F")
    draw.ellipse(rect_box((157, 157, 211, 211)), fill="#FFFFFF")
    draw.line(points([(184, 168), (184, 184), (197, 191)]), fill="#62517F", width=p(7), joint="curve")
    draw.ellipse(rect_box((180, 180, 188, 188)), fill="#62517F")
    save(image, "qing_rental")


def spreadsheet():
    image, draw = base("#176F86")
    # One sheet with a large, readable 3-by-3 data grid.
    draw.rounded_rectangle(rect_box((57, 39, 199, 219)), radius=p(17), fill="#FFFFFF")
    draw.rounded_rectangle(rect_box((57, 39, 199, 72)), radius=p(17), fill="#BCE8DC")
    draw.rectangle(rect_box((57, 59, 199, 72)), fill="#BCE8DC")
    draw.rounded_rectangle(rect_box((70, 85, 186, 203)), radius=p(7), fill="#E9F1F0")
    for x in (109, 148):
        draw.line(points([(x, 85), (x, 203)]), fill="#176F86", width=p(4))
    for y in (124, 163):
        draw.line(points([(70, y), (186, y)]), fill="#176F86", width=p(4))
    draw.rectangle(rect_box((73, 88, 107, 122)), fill="#F2B95D")
    draw.rectangle(rect_box((111, 126, 146, 161)), fill="#86CCBF")
    draw.rectangle(rect_box((150, 165, 183, 200)), fill="#F2B95D")
    save(image, "qing_spreadsheet")


def knowledge():
    image, draw = base("#26766D")
    # Open pages, a clear central spine and a bookmark identify the library.
    draw.polygon(points([(39, 66), (64, 55), (112, 59), (128, 70), (128, 198), (109, 187), (64, 183), (39, 191)]), fill="#D6E9E4")
    draw.polygon(points([(217, 66), (192, 55), (144, 59), (128, 70), (128, 198), (147, 187), (192, 183), (217, 191)]), fill="#FFFFFF")
    draw.line(points([(128, 70), (128, 198)]), fill="#E7B563", width=p(7))
    for y in (99, 119, 139):
        draw.line(points([(65, y), (107, y + 2)]), fill="#61998F", width=p(6))
        draw.line(points([(150, y + 2), (192, y)]), fill="#61998F", width=p(6))
    draw.polygon(points([(158, 57), (178, 55), (178, 105), (168, 98), (158, 105)]), fill="#F0B95D")
    save(image, "qing_knowledge")


if __name__ == "__main__":
    rental()
    spreadsheet()
    knowledge()
