"""Draw a board's placement: its outline, courtyards, pads and
references, and each net's ratsnest in its own color (placement.py)."""

import colorsys

from PIL import Image, ImageDraw, ImageFont

from ee_tooling.placement import Box, Layout, Point, Rules, ratsnest

MARGIN = 1.0  # mm of white around the board's outline
BACKGROUND = "white"
OUTLINE = "black"
COURTYARD = "#bbbbbb"
POURED_PAD = "#22aa77"
PAD = "#888888"
TEXT = "black"
OUTLINE_WIDTH = 2  # px
RATSNEST_WIDTH = 2  # px
REFERENCE_SIZE = 13  # px
# Successive nets' hues step by the golden ratio's fraction, so
# neighbors in the list differ; saturation and value keep them readable.
HUE_STEP = 0.618
SATURATION = 0.9
VALUE = 0.8


def color(n: int) -> tuple[int, int, int]:
    """The nth net's ratsnest color."""
    r, g, b = colorsys.hsv_to_rgb((n * HUE_STEP) % 1.0, SATURATION, VALUE)
    return round(r * 255), round(g * 255), round(b * 255)


def render(layout: Layout, rules: Rules, scale: float) -> Image.Image:
    """The placement at scale px per mm."""
    o = layout.outline

    def px(p: Point) -> tuple[float, float]:
        return (p[0] - o.left + MARGIN) * scale, (p[1] - o.top + MARGIN) * scale

    def rectangle(b: Box) -> tuple[tuple[float, float], tuple[float, float]]:
        return px((b.left, b.top)), px((b.right, b.bottom))

    size = (
        round((o.right - o.left + 2 * MARGIN) * scale),
        round((o.bottom - o.top + 2 * MARGIN) * scale),
    )
    image = Image.new("RGB", size, BACKGROUND)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=REFERENCE_SIZE)
    draw.rectangle(rectangle(o), outline=OUTLINE, width=OUTLINE_WIDTH)
    for f in layout.footprints:
        if f.courtyard:
            draw.rectangle(rectangle(f.courtyard), outline=COURTYARD)
        for pad in f.pads:
            draw.rectangle(rectangle(pad.box), fill=POURED_PAD if pad.net in rules.poured else PAD)
        draw.text(px(f.position), f.reference, fill=TEXT, font=font, anchor="mm")
    for n, edges in enumerate(ratsnest(layout, rules.poured).values()):
        for a, b in edges:
            draw.line([px(a), px(b)], fill=color(n), width=RATSNEST_WIDTH)
    return image
