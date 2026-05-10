# Currently dormant. Will be wired into /weekly endpoint once the Telegram image flow is implemented (Phase D+). Do not delete.
"""Generate PNG images for complex list views (weekly overview, multi-list summaries)."""
from __future__ import annotations

import io
import textwrap
from datetime import date
from typing import Sequence

from PIL import Image, ImageDraw, ImageFont

# Dimensions & palette
WIDTH = 800
PADDING = 40
LINE_HEIGHT = 28
SECTION_GAP = 20
HEADER_HEIGHT = 60
FONT_SIZE = 18
HEADER_FONT_SIZE = 22
SECTION_FONT_SIZE = 20
BG_COLOR = (250, 250, 248)
HEADER_BG = (45, 85, 145)
HEADER_FG = (255, 255, 255)
SECTION_FG = (45, 85, 145)
TEXT_COLOR = (30, 30, 30)
MUTED_COLOR = (120, 120, 120)
DIVIDER_COLOR = (210, 210, 205)


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]:
        try:
            return ImageFont.truetype(path, size)
        except (IOError, OSError):
            continue
    return ImageFont.load_default()


def _load_bold_font(size: int) -> ImageFont.FreeTypeFont:
    for path in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]:
        try:
            return ImageFont.truetype(path, size)
        except (IOError, OSError):
            continue
    return ImageFont.load_default()


class Section:
    def __init__(self, title: str, lines: Sequence[str]):
        self.title = title
        self.lines = list(lines)


def _wrap_lines(lines: list[str], max_chars: int = 80) -> list[str]:
    result = []
    for line in lines:
        if len(line) <= max_chars:
            result.append(line)
        else:
            wrapped = textwrap.wrap(line, width=max_chars)
            result.extend(wrapped)
    return result


def _calculate_height(sections: list[Section]) -> int:
    font = _load_font(FONT_SIZE)
    height = HEADER_HEIGHT + PADDING

    for section in sections:
        height += SECTION_FONT_SIZE + 8  # section title
        wrapped = _wrap_lines(section.lines)
        height += len(wrapped) * LINE_HEIGHT
        height += SECTION_GAP + 10  # divider + gap

    return height + PADDING


def render_overview(title: str, sections: list[Section]) -> io.BytesIO:
    height = _calculate_height(sections)
    img = Image.new("RGB", (WIDTH, height), BG_COLOR)
    draw = ImageDraw.Draw(img)

    # Header bar
    draw.rectangle([(0, 0), (WIDTH, HEADER_HEIGHT)], fill=HEADER_BG)
    hfont = _load_bold_font(HEADER_FONT_SIZE)
    draw.text((PADDING, HEADER_HEIGHT // 2 - HEADER_FONT_SIZE // 2), title, font=hfont, fill=HEADER_FG)

    y = HEADER_HEIGHT + PADDING
    sfont = _load_bold_font(SECTION_FONT_SIZE)
    font = _load_font(FONT_SIZE)
    muted_font = _load_font(FONT_SIZE - 2)

    for section in sections:
        # Section title
        draw.text((PADDING, y), section.title, font=sfont, fill=SECTION_FG)
        y += SECTION_FONT_SIZE + 8

        if section.lines:
            wrapped = _wrap_lines(section.lines)
            for line in wrapped:
                draw.text((PADDING + 10, y), line, font=font, fill=TEXT_COLOR)
                y += LINE_HEIGHT
        else:
            draw.text((PADDING + 10, y), "Nothing scheduled.", font=muted_font, fill=MUTED_COLOR)
            y += LINE_HEIGHT

        y += 6
        draw.line([(PADDING, y), (WIDTH - PADDING, y)], fill=DIVIDER_COLOR, width=1)
        y += SECTION_GAP + 4

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def build_daily_image(
    day: date,
    appointments: list[str],
    tasks: list[str],
    routines: list[str],
    notes: list[str],
) -> io.BytesIO:
    title = f"Daily overview — {day.strftime('%A %-d %B %Y')}"
    sections = [
        Section("Appointments", appointments),
        Section("Today's tasks", tasks),
        Section("Routines", routines),
    ]
    if notes:
        sections.append(Section("This week's notes", notes))
    return render_overview(title, sections)


def build_weekly_image(
    week_start: date,
    appointments: list[str],
    tasks: list[str],
    notes: list[str],
) -> io.BytesIO:
    week_end = week_start + __import__("datetime").timedelta(days=6)
    title = (
        f"Week {week_start.strftime('%-d %b')} – {week_end.strftime('%-d %b %Y')}"
    )
    sections = [
        Section("Appointments", appointments),
        Section("Tasks due this week", tasks),
        Section("Notes", notes),
    ]
    return render_overview(title, sections)
