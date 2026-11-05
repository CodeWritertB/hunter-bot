"""Render Discord match embeds as portable PNG scorecards."""
import io
import re
import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

FONT = Path(__file__).resolve().parent.parent / "assets" / "NotoSans-Regular.ttf"


def render_card(embed, guild):
    backdrop = Image.new("RGB", (360, 280))
    pixels = backdrop.load()
    for y in range(280):
        for x in range(360):
            teal = math.exp(-((x - 15) ** 2 + (y - 90) ** 2) / 18000)
            violet = math.exp(-((x - 285) ** 2 + (y - 20) ** 2) / 13000)
            ember = math.exp(-((x - 350) ** 2 + (y - 235) ** 2) / 11000)
            pixels[x, y] = (int(8 + 40 * violet + 49 * ember),
                            int(12 + 53 * teal + 12 * violet),
                            int(25 + 55 * violet + 36 * teal))
    image = backdrop.resize((1440, 1120), Image.Resampling.BICUBIC)
    glow = Image.new("RGBA", image.size)
    ornament = ImageDraw.Draw(glow)
    for offset in range(4):
        ornament.arc((800 + offset * 38, -390 + offset * 38, 1700 + offset * 38, 510 + offset * 38),
                     20, 310, fill=(179, 108, 255, 95), width=2)
    ornament.polygon([(0, 720), (580, 1120), (340, 1120), (0, 900)], fill=(42, 221, 185, 16))
    image = Image.alpha_composite(image.convert("RGBA"), glow).convert("RGB")
    draw = ImageDraw.Draw(image)
    def font(size):
        return ImageFont.truetype(str(FONT), size)
    def clean(text):
        def member(match):
            user = guild.get_member(int(match.group(1))) if guild else None
            return user.display_name if user else "Участник"
        text = re.sub(r"<@!?(\d+)>", member, str(text))
        text = re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", text)
        text = re.sub(r"[\U0001F000-\U0001FAFF\uFE0F]", "", text)
        translations = {"Ranked": "Рейтинговый матч", "Public": "Обычный матч",
                        "Matchmaking": "Подбор игры", "All Pick": "Все герои",
                        "Turbo": "Турбо", "Anonymous": "Скрытый профиль",
                        "KDA:": "У/С/П:", "GPM:": "Золото/мин:", "XPM:": "Опыт/мин:",
                        "LH:": "Добивания:", "WR:": "Победы:", "Нетворс:": "Ценность:",
                        "нетворс": "ценность"}
        for source, target in translations.items():
            text = text.replace(source, target)
        return text.replace("**", "").replace("└", "").strip()
    def label(text, xy, size=22, color="#a8b7cb", width=620):
        text = clean(text)
        face = font(size)
        while text and draw.textlength(text, font=face) > width:
            text = text[:-2]
        draw.text(xy, text, font=face, fill=color)
    for x in range(1440):
        draw.line((x, 0, x, 5), fill=(65 + x * 110 // 1440, 190 - x * 100 // 1440, 160))
    label("TSH   /   ЦЕНТР МАТЧЕЙ", (56, 35), 20, "#8d9cb2", 1200)
    finished = "завершён" in embed.title
    label("DOTA 2", (52, 78), 70, "#f2eee7", 1000)
    draw.rounded_rectangle((1160, 65, 1384, 112), 20, fill="#182f2c" if not finished else "#262e40")
    label("МАТЧ ИДЁТ" if not finished else "МАТЧ ЗАВЕРШЁН", (1180, 76), 17, "#6be1b2", 200)
    title = clean(embed.title)
    title = re.sub(r"^[^А-Яа-яA-Za-z0-9]+", "", title)
    label(title, (57, 179), 30, "#f2eee7", 1300)
    if embed.description:
        label(embed.description, (57, 229), 20, width=1300)
    fields = list(embed.fields)
    teams = [f for f in fields if "Силы" in f.name]
    summary = [f for f in fields if "Силы" not in f.name]
    for index, field in enumerate(summary[:2]):
        left = 56 + index * 670
        draw.rounded_rectangle((left, 280, left + 642, 386), 18, fill="#202342", outline="#545180", width=2)
        label(field.name, (left + 24, 295), 17)
        label(field.value, (left + 24, 324), 32, "#ffffff", 590)
    top = 426 if summary else 292
    for side, field in enumerate(teams[:2]):
        left = 56 + side * 670
        accent = "#47f4c2" if side == 0 else "#ff747d"
        draw.rounded_rectangle((left, top, left + 642, 1037), 20, fill="#111c30", outline=accent, width=2)
        draw.rounded_rectangle((left, top, left + 642, top + 65), 18, fill="#1b2d2d" if side == 0 else "#30242c")
        label(field.name, (left + 24, top + 18), 23, accent)
        y = top + 88
        for line in clean(field.value).splitlines():
            line = line.strip()
            if not line:
                continue
            if y > 1006:
                break
            stats = line.startswith(("У/С/П:", "Добивания:", "Победы:"))
            label(line, (left + 24, y), 17 if stats else 21, "#a1b2c8" if stats else "#f0f4fa", 592)
            y += 24 if stats else 32
            if stats:
                y += 4
    label("HUNTER BOT  •  " + ("Итоги матча" if finished else "Матч в процессе"), (56, 1070), 17, "#71839c", 1200)
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output
