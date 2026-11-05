"""Render Discord match embeds as portable PNG scorecards."""
import io
import re
import math
import json
import urllib.request
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

FONT = Path(__file__).resolve().parent.parent / "assets" / "NotoSans-Regular.ttf"


@lru_cache(maxsize=4)
def constants(name):
    with urllib.request.urlopen("https://raw.githubusercontent.com/odota/dotaconstants/master/build/" + name + ".json", timeout=8) as response:
        return json.load(response)


@lru_cache(maxsize=256)
def icon(path):
    url = "https://cdn.cloudflare.steamstatic.com" + path
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            payload = response.read(2000000)
        return Image.open(io.BytesIO(payload)).convert("RGB")
    except Exception:
        return None


def load_assets(players):
    try:
        heroes = constants("heroes")
        items = {int(value["id"]): value for value in constants("items").values() if "id" in value}
    except Exception:
        return {}, {}, {}
    paths = set()
    for player in players:
        hero = heroes.get(str(player.get("hero_id", 0)), {})
        if hero.get("img"):
            paths.add(hero["img"])
        for index in range(10):
            item = items.get(player.get("item_" + str(index), 0), {})
            if item.get("img"):
                paths.add(item["img"])
    with ThreadPoolExecutor(max_workers=8) as pool:
        pictures = dict(zip(paths, pool.map(icon, paths)))
    return heroes, items, pictures


def render_card(embed, guild, match_data=None, players_info=None):
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
            pixels[x, y] = (int(10 + 57 * red + 27 * gold),
                            int(15 + 29 * green + 12 * gold),
                            int(13 + 15 * green + 8 * red))
    image = backdrop.resize((1440, 1160), Image.Resampling.BICUBIC).convert("RGBA")
    if winner is not None:
        glow = Image.new("RGBA", image.size)
        painter = ImageDraw.Draw(glow)
        left = 50 if winner == 0 else 780
        painter.ellipse((left, 65, left + 600, 395), fill=(255, 138, 30, 48))
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
    players = (match_data or {}).get("players") or players_info or []
    heroes, items, pictures = load_assets(players)
    top = 422
    def paste(path, box):
        picture = pictures.get(path)
        if picture is not None:
            from PIL import ImageOps
            image.paste(ImageOps.fit(picture, (box[2] - box[0], box[3] - box[1])), box[:2])
        else:
            draw.rounded_rectangle(box, 5, fill="#29302d")
    for side, field in enumerate(teams[:2]):
        left = 56 + side * 670
        accent = "#ffc16a" if winner == side else ("#63bd76" if side == 0 else "#d96055")
        draw.line((left + 12, top, left + 630, top), fill=accent, width=3)
        team_players = [player for player in players if (player.get("player_slot", player.get("slot", 0)) < 128) == (side == 0)]
        if not team_players:
            label("Подробности игроков пока недоступны", (left + 18, top + 30), 19)
        for index, player in enumerate(team_players[:5]):
            y = top + 20 + index * 126
            draw.rounded_rectangle((left, y, left + 642, y + 116), 12, fill="#151d1a" if side == 0 else "#201a19")
            hero = heroes.get(str(player.get("hero_id", 0)), {})
            paste(hero.get("img"), (left + 12, y + 12, left + 98, y + 62))
            account = player.get("account_id", player.get("steam32"))
            linked = next((entry for entry in (players_info or []) if account and entry.get("steam32") == account and entry.get("linked")), None)
            name = (linked or player).get("mention") or player.get("personaname") or hero.get("localized_name", "Игрок")
            label(name, (left + 110, y + 7), 20, "#fff0d9", 510)
            label(hero.get("localized_name", "Герой") + "  ·  " +
                  "/".join(str(player.get(key, "—")) for key in ("kills", "deaths", "assists")),
                  (left + 110, y + 37), 15, "#bfb6a4", 510)
            if "gold_per_min" in player:
                label(f"Золото/мин {player.get('gold_per_min', '—')} · Опыт/мин {player.get('xp_per_min', '—')} · Урон {player.get('hero_damage', '—')}",
                      (left + 110, y + 57), 11, "#a9a697", 510)
            if player.get("item_neutral"):
                paste(items.get(player["item_neutral"], {}).get("img"), (left + 12, y + 72, left + 56, y + 104))
            for slot in range(6):
                item_id = player.get("item_" + str(slot))
                box = (left + 110 + slot * 48, y + 72, left + 154 + slot * 48, y + 104)
                paste(items.get(item_id, {}).get("img"), box)
            buffs = {entry.get("permanent_buff"): entry.get("stack_count", 1) for entry in player.get("permanent_buffs", [])}
            upgrades = [("Ш", player.get("aghanims_shard") or buffs.get(12)),
                        ("А", buffs.get(2) or (player.get("aghanims_scepter") and 108 not in [player.get("item_" + str(slot)) for slot in range(6)])),
                        ("Л", player.get("moonshard") or buffs.get(1))]
            for number, (short, present) in enumerate(upgrades):
                x = left + 420 + number * 58
                draw.rounded_rectangle((x, y + 73, x + 48, y + 103), 8,
                                       fill="#64431e" if present else "#222522",
                                       outline="#f6c574" if present else "#41443b")
                label(short if present else "—", (x + 16, y + 77), 16, "#ffe1a4" if present else "#777d72", 32)
    label("Ш — Шард   ·   А — поглощённый Аганим   ·   Л — поглощённый Мун Шард",
          (56, 1090), 17, "#c6b797", 1320)
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output
