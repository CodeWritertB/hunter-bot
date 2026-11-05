"""Render Discord match embeds as portable PNG scorecards."""
import io
import re
import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

FONT = Path(__file__).resolve().parent.parent / "assets" / "NotoSans-Regular.ttf"


def render_card(embed, guild):
    finished = "завершён" in embed.title
    winner = None
    if finished:
        result = embed.title.split("завершён", 1)[-1]
        if "Силы Света" in result:
            winner = 0
        elif "Силы Тьмы" in result:
            winner = 1
    backdrop = Image.new("RGB", (360, 280))
    pixels = backdrop.load()
    for y in range(280):
        for x in range(360):
            green = math.exp(-((x - 25) ** 2 + (y - 50) ** 2) / 12000)
            red = math.exp(-((x - 335) ** 2 + (y - 50) ** 2) / 12000)
            gold = math.exp(-((x - (55 if winner == 0 else 305)) ** 2 + (y - 75) ** 2) / 8500) if winner is not None else 0
            pixels[x, y] = (int(10 + 57 * red + 87 * gold),
                            int(15 + 43 * green + 49 * gold),
                            int(13 + 15 * green + 8 * red))
    image = backdrop.resize((1440, 1120), Image.Resampling.BICUBIC).convert("RGBA")
    if winner is not None:
        glow = Image.new("RGBA", image.size)
        painter = ImageDraw.Draw(glow)
        left = 50 if winner == 0 else 780
        painter.ellipse((left, 65, left + 600, 395), fill=(255, 148, 30, 95))
        image = Image.alpha_composite(image, glow.filter(ImageFilter.GaussianBlur(55)))
    image = image.convert("RGB")
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
    def centered(text, center, y, size, color, width):
        face = font(size)
        text = clean(text)
        while text and draw.textlength(text, font=face) > width:
            text = text[:-1]
        draw.text((center - draw.textlength(text, font=face) / 2, y), text, font=face, fill=color)
    draw.line((56, 22, 1384, 22), fill="#a96a32", width=2)
    label("TSH  /  DOTA 2", (56, 42), 21, "#dcc7a7")
    label("ЦЕНТР МАТЧЕЙ", (1160, 42), 18, "#c7ad87", 224)
    fields = list(embed.fields)
    teams = [f for f in fields if "Силы" in f.name]
    score_field = next((f for f in fields if f.name == "Счёт"), None)
    scores = re.findall(r"\d+", clean(score_field.value)) if score_field else []
    duration = next((clean(f.value) for f in fields if f.name == "Длительность"), None)
    match_number = re.search(r"#(\d+)", embed.title)
    centered("МАТЧ " + (match_number.group(1) if match_number else ""), 720, 94, 19, "#d6bea0", 410)
    centered("ЗАВЕРШЁН" if finished else "ИДЁТ СЕЙЧАС", 720, 126, 23, "#ffcd77", 410)
    centered("VS", 720, 177, 62, "#f4debb", 250)
    mode = clean(embed.description or "")
    if not mode and "•" in embed.title:
        mode = clean(embed.title.split("•", 1)[1])
    centered(mode or "Режим уточняется", 720, 275, 18, "#d8c5aa", 400)
    centered(duration or "Длительность уточняется", 720, 311, 25, "#f6ebda", 400)
    for side, team in enumerate(teams[:2]):
        center = 300 if side == 0 else 1140
        accent = "#71df83" if side == 0 else "#ff7770"
        name = re.sub(r"\s*\[.*?\]", "", clean(team.name))
        centered(name.upper(), center, 126, 33, accent, 430)
        score = scores[side] if len(scores) == 2 else None
        if score is None:
            live_score = re.search(r"\[(\d+)\]", team.name)
            score = live_score.group(1) if live_score else "—"
        centered(score, center, 175, 98, "#ffd18a" if winner == side else "#faf1de", 430)
        centered("УБИЙСТВ", center, 300, 16, "#ccb99e", 430)
        if winner == side:
            draw.rounded_rectangle((center - 115, 346, center + 115, 383), 12, fill="#9e551e", outline="#ffc56e", width=2)
            centered("ПОБЕДА", center, 352, 19, "#fff0c5", 220)
    top = 422
    for side, field in enumerate(teams[:2]):
        left = 56 + side * 670
        accent = "#ffc16a" if winner == side else ("#63bd76" if side == 0 else "#d96055")
        draw.rounded_rectangle((left, top, left + 642, 1037), 20, fill="#182019" if side == 0 else "#241a18", outline=accent, width=2)
        draw.rounded_rectangle((left, top, left + 642, top + 65), 18, fill="#4b331e" if winner == side else ("#203b28" if side == 0 else "#42241f"))
        label("СОСТАВ КОМАНДЫ", (left + 24, top + 18), 20, accent)
        y = top + 88
        for line in clean(field.value).splitlines():
            line = line.strip()
            if not line:
                continue
            if y > 1006:
                break
            stats = line.startswith(("У/С/П:", "Добивания:", "Победы:"))
            label(line, (left + 24, y), 15 if stats else 21, "#bcb7a6" if stats else "#f7efdf", 592)
            y += 24 if stats else 32
            if stats:
                y += 4
    label("HUNTER BOT  •  " + ("Итоги матча" if finished else "Матч в процессе"), (56, 1070), 17, "#71839c", 1200)
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output
