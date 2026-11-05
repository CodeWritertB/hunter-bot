"""Render Discord match embeds as portable PNG scorecards."""
import io
import re
import math
import json
import urllib.request
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps, ImageEnhance

FONT = Path(__file__).resolve().parent.parent / "assets" / "NotoSans-Regular.ttf"


@lru_cache(maxsize=4)
def constants(name):
    with urllib.request.urlopen("https://raw.githubusercontent.com/odota/dotaconstants/master/build/" + name + ".json", timeout=8) as response:
        return json.load(response)


@lru_cache(maxsize=256)
def icon(path):
    if not path:
        return None
    if path.startswith("https://"):
        from urllib.parse import urlparse
        host = urlparse(path).hostname or ""
        if not (host.endswith(".steamstatic.com") or host.endswith(".steamcdn-a.akamaihd.net")):
            return None
        url = path
    else:
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
    paths = {items[key]["img"] for key in items if items[key].get("img") and
             items[key].get("dname") in {"Aghanim's Shard", "Aghanim's Blessing", "Moon Shard"}}
    for player in players:
        if player.get("steam_avatar"):
            paths.add(player["steam_avatar"])
        hero = heroes.get(str(player.get("hero_id", 0)), {})
        if hero.get("img"):
            paths.add(hero["img"])
        for index in range(9):
            key = "item_" + str(index) if index < 6 else "backpack_" + str(index - 6)
            item = items.get(player.get(key, 0), {})
            if item.get("img"):
                paths.add(item["img"])
        neutral = items.get(player.get("item_neutral"), {})
        if not neutral:
            history = player.get("neutral_item_history") or []
            latest = max(history, key=lambda entry: entry.get("time", 0), default={})
            neutral = next((item for item in items.values() if item.get("img", "").endswith("/" + latest.get("item_neutral", "__missing__") + ".png")), {})
        if neutral.get("img"):
            paths.add(neutral["img"])
    with ThreadPoolExecutor(max_workers=8) as pool:
        pictures = dict(zip(paths, pool.map(icon, paths)))
    return heroes, items, pictures


def render_card(embed, guild, match_data=None, players_info=None, public_profiles=None):
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
    image = backdrop.resize((1440, 1600), Image.Resampling.BICUBIC).convert("RGBA")
    if winner is not None:
        glow = Image.new("RGBA", image.size)
        painter = ImageDraw.Draw(glow)
        left = 50 if winner == 0 else 780
        painter.ellipse((left, 65, left + 600, 395), fill=(255, 138, 30, 48))
        image = Image.alpha_composite(image, glow.filter(ImageFilter.GaussianBlur(55)))
    image = image.convert("RGB")
    draw = ImageDraw.Draw(image)
    def font(size):
        return ImageFont.truetype(str(FONT.parent / "Spectral-SemiBold.ttf" if size >= 25 else FONT), size)
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
    def paste(path, box, active=True):
        picture = pictures.get(path)
        if picture is not None:
            from PIL import ImageOps
            tile = ImageOps.fit(picture, (box[2] - box[0], box[3] - box[1]))
            if not active:
                tile = ImageEnhance.Brightness(ImageOps.grayscale(tile).convert("RGB")).enhance(0.45)
            image.paste(tile, box[:2])
        else:
            draw.rounded_rectangle(box, 5, fill="#29302d")
    def portrait(path, box, server, dotabuff):
        width, height = box[2] - box[0], box[3] - box[1]
        tile = Image.new("RGB", (width, height), "#29302d")
        if pictures.get(path) is not None:
            tile = ImageOps.fit(pictures[path], (width, height))
        mask = Image.new("L", (width, height))
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, width - 1, height - 1), 8, fill=255)
        image.paste(tile, box[:2], mask)
        first = "#66e891" if server else "#ed9c47" if dotabuff else "#51574d"
        draw.rounded_rectangle((box[0] - 2, box[1] - 2, box[2] + 1, box[3] + 1), 10, outline=first, width=2)
        if server and dotabuff:
            draw.rounded_rectangle((box[0] - 6, box[1] - 6, box[2] + 5, box[3] + 5), 13, outline="#ed9c47", width=2)

    def player_name(text, xy, server, dotabuff):
        text = clean(text)
        face = font(22)
        while text and draw.textlength(text, font=face) > 286:
            text = text[:-1]
        if not (server or dotabuff):
            return label(text, xy, 22, "#fff0d9", 286)
        start = (102, 232, 145) if server else (237, 156, 71)
        end = (237, 156, 71) if dotabuff else (187, 255, 199)
        mask = Image.new("L", (300, 40))
        ImageDraw.Draw(mask).text((0, 0), text, font=face, fill=255)
        gradient = Image.new("RGB", mask.size)
        pen = ImageDraw.Draw(gradient)
        for x in range(300):
            color = tuple(int(a + (b - a) * min(1, x / max(1, draw.textlength(text, font=face)))) for a, b in zip(start, end))
            pen.line((x, 0, x, 40), fill=color)
        image.paste(gradient, xy, mask)

    for side, field in enumerate(teams[:2]):
        left = 56 + side * 670
        accent = "#ffc16a" if winner == side else ("#63bd76" if side == 0 else "#d96055")
        draw.line((left + 12, top, left + 630, top), fill=accent, width=3)
        team_players = [player for player in players if (player.get("player_slot", player.get("slot", 0)) < 128) == (side == 0)]
        if not team_players:
            label("Подробности игроков пока недоступны", (left + 18, top + 30), 19)
        for index, player in enumerate(team_players[:5]):
            y = top + 20 + index * 214
            draw.rounded_rectangle((left, y, left + 642, y + 202), 12, fill="#151d1a" if side == 0 else "#201a19")
            hero = heroes.get(str(player.get("hero_id", 0)), {})
            account = player.get("account_id", player.get("steam32"))
            linked = next((entry for entry in (players_info or []) if account and entry.get("steam32") == account and entry.get("linked")), None)
            name = (linked or player).get("mention") or player.get("personaname") or hero.get("localized_name", "Игрок")
            server = bool(linked)
            dotabuff = bool((public_profiles or {}).get(int(account or 0)))
            portrait(player.get("steam_avatar"), (left + 24, y + 15, left + 88, y + 79), server, dotabuff)
            portrait(hero.get("img"), (left + 14, y + 106, left + 100, y + 158), server, dotabuff)
            player_name(name, (left + 112, y + 12), server, dotabuff)
            label(hero.get("localized_name", "Герой"), (left + 112, y + 45), 16, "#c5bba7", 286)
            label("У / С / П   " + " / ".join(str(player.get(key, "—")) for key in ("kills", "deaths", "assists")),
                  (left + 112, y + 75), 18, "#f6d39b", 286)
            stats = [("Ценность", "net_worth"), ("Золото/мин", "gold_per_min"),
                     ("Опыт/мин", "xp_per_min"), ("Добивания", "last_hits"), ("Урон героям", "hero_damage")]
            for number, (caption, key) in enumerate(stats):
                value = player.get(key)
                value = f"{value:,}".replace(",", " ") if isinstance(value, (int, float)) else "—"
                label(caption + "   " + value, (left + 112, y + 106 + number * 17), 13, "#c2c2b4", 210)
            history = player.get("neutral_item_history") or []
            latest = max(history, key=lambda entry: entry.get("time", 0), default={})
            def named_item(key):
                return next((item for item in items.values() if item.get("img", "").endswith("/" + key + ".png")), {})
            neutral = items.get(player.get("item_neutral"), {}) or named_item(latest.get("item_neutral", "__missing__"))
            enhancement = named_item(latest.get("item_neutral_enhancement", "__missing__"))
            paste(neutral.get("img"), (left + 337, y + 80, left + 411, y + 132))
            label("Нейтральный", (left + 334, y + 139), 10, "#b8af97", 86)
            label(enhancement.get("dname", "Свойства —"), (left + 334, y + 155), 10, "#e7cc95", 86)
            attributes = enhancement.get("attrib") or neutral.get("attrib") or []
            for number, attribute in enumerate(attributes[:2]):
                names = {"cast_range_bonus": "Дальность", "bonus_mana_regen": "Мана/с", "bonus_health": "Здоровье", "bonus_mana": "Мана", "bonus_damage": "Урон", "bonus_armor": "Броня", "bonus_attack_speed": "Скор. атаки", "bonus_movement_speed": "Скорость", "bonus_strength": "Сила", "bonus_agility": "Ловкость", "bonus_intellect": "Интеллект", "bonus_all_stats": "Атрибуты", "bonus_health_regen": "Здоровье/с"}
                caption = names.get(attribute.get("key"), attribute.get("header") or "Бонус")
                text = str(caption) + " " + str(attribute.get("value", ""))
                text = re.sub("<[^>]*>", "", text)
                label(text, (left + 334, y + 170 + number * 12), 9, "#aaa895", 86)
            buffs = {entry.get("permanent_buff"): entry.get("stack_count", 1) for entry in player.get("permanent_buffs", [])}
            owned_scepter = any(items.get(player.get("item_" + str(slot)), {}).get("dname") == "Aghanim's Scepter" for slot in range(6))
            upgrades = [("Aghanim's Shard", player.get("aghanims_shard") or buffs.get(12)),
                        ("Aghanim's Blessing", buffs.get(2) or (player.get("aghanims_scepter") and not owned_scepter)),
                        ("Moon Shard", player.get("moonshard") or buffs.get(1))]
            for number, (name, active) in enumerate(upgrades):
                item = next((item for item in items.values() if item.get("dname") == name), {})
                x = left + 430 + number * 64
                paste(item.get("img"), (x, y + 12, x + 56, y + 51), bool(active))
                draw.rectangle((x, y + 12, x + 56, y + 51), outline="#ffc86d" if active else "#4a4c45", width=1)
            for slot in range(6):
                x = left + 430 + slot % 3 * 64
                item_y = y + 62 + slot // 3 * 40
                paste(items.get(player.get("item_" + str(slot)), {}).get("img"),
                      (x, item_y, x + 56, item_y + 36))
            draw.line((left + 430, y + 147, left + 614, y + 147), fill="#494333", width=1)
            for slot in range(3):
                x = left + 430 + slot * 64
                paste(items.get(player.get("backpack_" + str(slot)), {}).get("img"),
                      (x, y + 156, x + 56, y + 190))
    label("Hunter Bot",
          (56, 1540), 17, "#c6b797", 1320)
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output
