#!/usr/bin/env python3
"""Render the README demo GIF: an illustration of the flow with FAKE data, not a screen recording.

    python3 scripts/demo/make_gif.py           # needs pillow (requirements-dev.txt)

Writes docs/assets/demo.gif. The collector progress lines come from a real run of the collector
against fixture.json (see make_digest.py); the triage page and the question are scripted samples
that match that fixture. Pass --frames to also save three PNG frames in scripts/demo/out/.
"""

from __future__ import annotations

import pathlib
import sys

from make_digest import HERE, ROOT, run_collector
from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 900, 540
PAD, LINE = 28, 25
FONT_SIZE = 16
FONTS = [
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/SFNSMono.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
]
BG, BAR = "#0d1117", "#161b22"
COLORS = {
    "text": "#c9d1d9",
    "dim": "#8b949e",
    "green": "#3fb950",
    "red": "#f85149",
    "blue": "#58a6ff",
    "yellow": "#d29922",
    "white": "#ffffff",
}
PALETTE_COLORS = 48

Line = tuple[str, str]  # (text, colour name)


def load_font() -> ImageFont.ImageFont:
    for path in FONTS:
        if pathlib.Path(path).exists():
            return ImageFont.truetype(path, FONT_SIZE)
    return ImageFont.load_default()


FONT = load_font()


def frame(lines: list[Line]) -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, WIDTH, 34), fill=BAR)
    for i, colour in enumerate(("#ff5f56", "#ffbd2e", "#27c93f")):
        draw.ellipse((16 + i * 22, 11, 28 + i * 22, 23), fill=colour)
    draw.text((WIDTH // 2 - 110, 8), "Demo with sample data", font=FONT, fill=COLORS["dim"])
    for row, (text, colour) in enumerate(lines):
        draw.text((PAD, 50 + row * LINE), text, font=FONT, fill=COLORS[colour])
    return image


def scenes() -> list[tuple[list[Line], int]]:
    """(lines, milliseconds) per frame."""
    _, progress = run_collector()
    out: list[tuple[list[Line], int]] = []

    # 1. typing the command
    command = "/daily-ops:daily"
    for n in range(len(command) + 1):
        out.append(([("> " + command[:n] + "_", "white")], 90))
    out.append(([("> " + command, "white")], 500))

    # 2. the collector, one real progress line per source
    shown: list[Line] = [("> " + command, "white"), ("", "text"), ("Collecting (read-only)", "dim")]
    out.append((list(shown), 500))
    for line in progress:
        shown.append(("  " + line, "red" if "FAILED" in line else "green"))
        out.append((list(shown), 450))
    shown.append(("  daily-ops-out/2026-01-05/digest_0902.md", "dim"))
    out.append((list(shown), 1300))

    # 3. the triage page
    page: list[Line] = [
        ("Needs you today", "blue"),
        (" 1. SECOPS-1 is ready for review and waits for a verification (Sam Rivera)", "text"),
        (" 2. SECOPS-2 is overdue since 2026-01-02 (Jo Park)", "text"),
        (" 3. SECOPS-3: Sam Rivera asked about a service account exemption", "text"),
        ("", "text"),
        ("Proposed actions", "blue"),
        (" 1. SECOPS-1  Verification FAIL: fix merged, not deployed. Comment drafted", "text"),
        (" 2. SECOPS-2  Nudge asking Jo Park for status and a realistic date", "text"),
        (" 3. SECOPS-3  Reply to Sam Rivera, drafted", "text"),
        (" 4. mbp-alex  Message to the owner: not seen since 2026-01-01", "text"),
        ("", "text"),
        ("FYI", "blue"),
        (" okta: SOURCE FAILED, OKTA_API_TOKEN is not set", "dim"),
        (" thinkpad-jo: disk encryption not active", "dim"),
    ]
    for n in (1, 4, 6, 10, 12, len(page)):
        out.append((page[:n], 350))
    out.append((page, 3600))

    # 4. the question, cursor moving to Approve
    options = ["Approve: post as drafted, move back to In Progress", "Change it", "Skip today", "Drop"]

    def question(cursor: int, chosen: bool = False) -> list[Line]:
        lines: list[Line] = [
            ("#1 SECOPS-1", "yellow"),
            ("", "text"),
            ("Post the verification comment on SECOPS-1 (assignee Sam Rivera)?", "white"),
            ("It says the fix is merged but the running version still has the old", "dim"),
            ("library, so the ticket goes back to In Progress.", "dim"),
            ("", "text"),
        ]
        for i, option in enumerate(options):
            mark = ("[x] " if chosen else " >  ") if i == cursor else "    "
            lines.append(
                (f"{mark}{i + 1}. {option}", ("green" if chosen else "white") if i == cursor else "text")
            )
        lines += [("", "text"), ("Nothing changes until an option is chosen.", "dim")]
        return lines

    out += [(question(2), 1500), (question(1), 450), (question(0), 1300), (question(0, chosen=True), 900)]

    # 5. the outcome
    done: list[Line] = [
        ("#1 SECOPS-1  approved", "green"),
        ("", "text"),
        ("Posted comment on SECOPS-1, moved to In Progress.", "text"),
        ("Read back: status In Progress, assignee Sam Rivera.", "dim"),
        ("recorded jira:SECOPS-1: approved", "dim"),
        ("", "text"),
        ("Next: #2 SECOPS-2", "yellow"),
    ]
    for n in (1, 3, 4, 5):
        out.append((done[:n], 600))
    out.append((done, 3200))
    return out


def main() -> None:
    script = scenes()
    frames = [frame(lines) for lines, _ in script]
    if "--frames" in sys.argv:
        (HERE / "out").mkdir(exist_ok=True)
        for i in (len(frames) // 3, 2 * len(frames) // 3 + 2, len(frames) - 1):
            frames[i].save(HERE / "out" / f"frame_{i}.png")
    palette = [f.quantize(colors=PALETTE_COLORS, dither=Image.Dither.NONE) for f in frames]
    gif = ROOT / "docs/assets/demo.gif"
    gif.parent.mkdir(parents=True, exist_ok=True)
    palette[0].save(
        gif,
        save_all=True,
        append_images=palette[1:],
        duration=[ms for _, ms in script],
        loop=0,
        optimize=True,
    )
    total = sum(ms for _, ms in script) / 1000
    print(f"{gif} ({len(frames)} frames, {total:.1f} s, {gif.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
