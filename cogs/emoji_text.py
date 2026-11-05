"""Colour emoji rendering for Pillow using Twemoji artwork."""
import io
import re
import urllib.request
from functools import lru_cache
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ASSETS = Path(__file__).resolve().parent.parent / "assets" / "emoji"
PATTERN = re.compile(r"[\U0001F1E6-\U0001F1FF]{2}|[\U0001F000-\U0001FAFF\u2600-\u27BF](?:\uFE0F)?(?:[\U0001F3FB-\U0001F3FF])?(?:\u200D[\U0001F000-\U0001FAFF\u2600-\u27BF](?:\uFE0F)?)*")


@lru_cache(maxsize=128)
def emoji_picture(value):
    code = "-".join(f"{ord(char):x}" for char in value if char != "\ufe0f")
    try:
        local = ASSETS / (code + ".png")
        if local.exists():
            return Image.open(local).convert("RGBA")
        with urllib.request.urlopen("https://raw.githubusercontent.com/jdecked/twemoji/main/assets/72x72/" + code + ".png", timeout=3) as response:
            return Image.open(io.BytesIO(response.read(100000))).convert("RGBA")
    except (OSError, ValueError):
        return None


def draw_emoji_text(image, value, xy, font_path, size, fill, width):
    value = str(value).replace("\n", " ")
    face = ImageFont.truetype(str(font_path), size)
    draw = ImageDraw.Draw(image)
    parts = []
    offset = 0
    for match in PATTERN.finditer(value):
        parts.extend((char, None) for char in value[offset:match.start()])
        picture = emoji_picture(match.group())
        parts.append((match.group(), picture))
        offset = match.end()
    parts.extend((char, None) for char in value[offset:])
    lengths = [size + 3 if picture is not None else draw.textlength(text, font=face) for text, picture in parts]
    if sum(lengths) > width:
        ellipsis = draw.textlength("…", font=face)
        used = 0
        keep = 0
        for length in lengths:
            if used + length + ellipsis > width:
                break
            used += length
            keep += 1
        parts = parts[:keep] + [("…", None)]
        lengths = lengths[:keep] + [ellipsis]
    x, y = xy
    for (text, picture), length in zip(parts, lengths):
        if picture is not None:
            tile = picture.resize((size, size), Image.Resampling.LANCZOS)
            image.paste(tile, (int(x), int(y + size * 0.23)), tile)
        else:
            draw.text((x, y), text, font=face, fill=fill)
        x += length
