import os
import asyncio
import logging
import aiohttp
import time as _time
import disnake
from datetime import datetime, timezone, timedelta
from disnake.ext import commands
from db.database import link_steam, get_steam_id, unlink_steam, get_all_steam_links, _upsert_server, _get_server, get_dota_player, update_dota_rank, add_dota_match, get_guild_week_stats

log = logging.getLogger("cogs.dota")

STEAM_API_KEY = os.getenv("STEAM_API_KEY")
STEAM_API = "https://api.steampowered.com"
OPENDOTA_API = "https://api.opendota.com/api"
CHECK_INTERVAL = 60  # Проверка активных матчей каждые 60 секунд

RANK_NAMES = {
    1: "Страж", 2: "Страж", 3: "Рыцарь", 4: "Герой",
    5: "Легенда", 6: "Властелин", 7: "Властелин", 8: "Бессмертный"
}
RANK_STARS = ["", "★", "★★", "★★★", "★★★★", "★★★★★"]


def rank_name(rank_tier: int) -> str:
    if not rank_tier:
        return "Без ранга"
    tier = rank_tier // 10
    star = rank_tier % 10
    name = RANK_NAMES.get(tier, "—")
    stars = RANK_STARS[star] if star < len(RANK_STARS) else ""
    return f"{name} {stars}".strip()


def current_week() -> str:
    """Возвращает дату начала текущей недели (понедельник)."""
    today = datetime.now(timezone.utc).date()
    monday = today - timedelta(days=today.weekday())
    return monday.isoformat()


GAME_MODES = {
    0: "Unknown", 1: "All Pick", 2: "Captain's Mode", 3: "Random Draft",
    4: "Single Draft", 5: "All Random", 6: "Intro", 7: "Diretide",
    8: "Reverse Captain's Mode", 9: "The Greeviling", 10: "Tutorial",
    11: "Mid Only", 12: "Least Played", 13: "New Player Pool",
    14: "Compendium Matchmaking", 15: "Co-op vs Bots", 16: "Captains Draft",
    17: "Balanced Draft", 18: "Ability Draft", 19: "Event", 20: "All Random Deathmatch",
    21: "1v1 Mid", 22: "All Pick Ranked", 23: "Turbo", 24: "Mutation"
}

LOBBY_TYPES = {
    -1: "Invalid", 0: "Public Matchmaking", 1: "Practice", 2: "Tournament",
    3: "Tutorial", 4: "Co-op vs Bots", 5: "Team Match", 6: "Solo Queue",
    7: "Ranked", 8: "1v1 Mid", 9: "Battle Cup"
}

# guild_id -> {steam32 -> match_id} — активные матчи
active_matches: dict[int, dict[int, int]] = {}
# guild_id -> {match_id -> disnake.Message} — live сообщения
live_messages: dict[int, dict[int, disnake.Message]] = {}


def steam64_to_32(steam64: int) -> int:
    return steam64 - 76561197960265728


def resolve_steam_id(value: str) -> str | None:
    value = value.strip().rstrip("/")
    if value.isdigit():
        return value
    if "steamcommunity.com/profiles/" in value:
        return value.split("/profiles/")[-1].split("/")[0]
    return None


async def resolve_vanity(session: aiohttp.ClientSession, vanity: str) -> str | None:
    if "steamcommunity.com/id/" in vanity:
        vanity = vanity.split("/id/")[-1].split("/")[0]
    url = f"{STEAM_API}/ISteamUser/ResolveVanityURL/v1/"
    async with session.get(url, params={"key": STEAM_API_KEY, "vanityurl": vanity}) as r:
        data = await r.json()
        if data.get("response", {}).get("success") == 1:
            return data["response"]["steamid"]
    return None


async def get_steam_profiles_batch(session: aiohttp.ClientSession, steam64_list: list) -> dict:
    """Получает профили нескольких Steam пользователей за один запрос."""
    if not steam64_list:
        return {}
    ids = ",".join(steam64_list)
    url = f"{STEAM_API}/ISteamUser/GetPlayerSummaries/v2/"
    async with session.get(url, params={"key": STEAM_API_KEY, "steamids": ids}) as r:
        data = await r.json()
        players = data.get("response", {}).get("players", [])
        return {p["steamid"]: p for p in players}


async def get_steam_profile(session: aiohttp.ClientSession, steam64: str) -> dict | None:
    url = f"{STEAM_API}/ISteamUser/GetPlayerSummaries/v2/"
    async with session.get(url, params={"key": STEAM_API_KEY, "steamids": steam64}) as r:
        data = await r.json()
        players = data.get("response", {}).get("players", [])
        return players[0] if players else None


async def get_live_match(session: aiohttp.ClientSession, steam32: int) -> dict | None:
    """Проверяет находится ли игрок в активном матче через Steam API."""
    try:
        async with session.get(
            f"{STEAM_API}/IDOTA2Match_570/GetMatchHistory/v1/",
            params={"key": STEAM_API_KEY, "account_id": steam32, "matches_requested": 1}
        ) as r:
            if r.status != 200:
                return None
            data = await r.json()
            matches = data.get("result", {}).get("matches", [])
            if not matches:
                return None
            match = matches[0]
            if match.get("lobby_type") == -1:
                return None
            return match
    except Exception:
        return None


async def get_dota_stats(session: aiohttp.ClientSession, steam32: int) -> dict | None:
    async with session.get(f"{OPENDOTA_API}/players/{steam32}") as r:
        if r.status != 200:
            return None
        return await r.json()


async def get_recent_matches(session: aiohttp.ClientSession, steam32: int) -> list:
    async with session.get(f"{OPENDOTA_API}/players/{steam32}/recentMatches") as r:
        if r.status != 200:
            return []
        return (await r.json())[:5]


async def get_hero_stats(session: aiohttp.ClientSession, steam32: int) -> list:
    async with session.get(f"{OPENDOTA_API}/players/{steam32}/heroes") as r:
        if r.status != 200:
            return []
        heroes = await r.json()
        return sorted(heroes, key=lambda h: h.get("games", 0), reverse=True)[:5]


async def get_heroes_list(session: aiohttp.ClientSession) -> dict:
    async with session.get(f"{OPENDOTA_API}/heroes") as r:
        if r.status != 200:
            return {}
        heroes = await r.json()
        return {str(h["id"]): h["localized_name"] for h in heroes}


async def get_match_details(session: aiohttp.ClientSession, match_id: int) -> dict | None:
    """Получает детали матча через OpenDota (работает только для завершённых)."""
    async with session.get(f"{OPENDOTA_API}/matches/{match_id}") as r:
        if r.status != 200:
            return None
        return await r.json()


async def get_player_wl(session: aiohttp.ClientSession, steam32: int) -> tuple[int, int]:
    """Возвращает (wins, losses) за последние 20 игр."""
    async with session.get(f"{OPENDOTA_API}/players/{steam32}/wl", params={"limit": 20}) as r:
        if r.status != 200:
            return 0, 0
        data = await r.json()
        return data.get("win", 0), data.get("lose", 0)


async def get_player_kda(session: aiohttp.ClientSession, steam32: int) -> str:
    """Возвращает средний KDA за последние 20 игр."""
    async with session.get(f"{OPENDOTA_API}/players/{steam32}/recentMatches", params={"limit": 20}) as r:
        if r.status != 200:
            return "—"
        matches = await r.json()
        if not matches:
            return "—"
        kills = sum(m.get("kills", 0) for m in matches) / len(matches)
        deaths = sum(m.get("deaths", 0) for m in matches) / len(matches)
        assists = sum(m.get("assists", 0) for m in matches) / len(matches)
        return f"{kills:.1f}/{deaths:.1f}/{assists:.1f}"


def build_live_embed(match_id: int, players_info: list, heroes_list: dict) -> disnake.Embed:
    """Строит embed с информацией о текущем матче, разделяя по командам."""
    game_mode = players_info[0].get("game_mode", 0) if players_info else 0
    lobby_type = players_info[0].get("lobby_type", 0) if players_info else 0
    mode_name = GAME_MODES.get(game_mode, f"Mode {game_mode}")
    lobby_name = LOBBY_TYPES.get(lobby_type, "")

    embed = disnake.Embed(
        title=f"🎮 Активный матч #{match_id}",
        description=f"**{mode_name}**" + (f" • {lobby_name}" if lobby_name else ""),
        color=disnake.Color.yellow(),
        url=f"https://www.dotabuff.com/matches/{match_id}"
    )

    radiant = [p for p in players_info if p.get("slot", 0) < 128]
    dire = [p for p in players_info if p.get("slot", 128) >= 128]

    # Счёт убийств
    radiant_score = next((p["radiant_score"] for p in players_info if p.get("radiant_score") is not None), None)
    dire_score = next((p["dire_score"] for p in players_info if p.get("dire_score") is not None), None)

    def format_team(players: list) -> str:
        lines = []
        for p in players:
            hero = heroes_list.get(str(p.get("hero_id", 0)), "—")
            mention = p["mention"]
            wl = p.get("wl")
            kda = p.get("kda")
            top = p.get("top_heroes", [])
            if wl is not None:
                w, l = wl
                wr = round(w / (w + l) * 100) if (w + l) else 0
                fav = ", ".join(heroes_list.get(str(h["hero_id"]), "?") for h in top[:2]) or "—"
                lines.append(
                    f"**{mention}** — {hero}\n"
                    f"└ WR: {wr}% | KDA: {kda or '—'} | Топ: {fav}"
                )
            else:
                lines.append(f"{mention} — {hero}")
        return "\n".join(lines) if lines else "—"

    radiant_title = f"🟢 Силы Света [{radiant_score}]" if radiant_score is not None else "🟢 Силы Света"
    dire_title = f"🔴 Силы Тьмы [{dire_score}]" if dire_score is not None else "🔴 Силы Тьмы"

    embed.add_field(name=radiant_title, value=format_team(radiant), inline=False)
    embed.add_field(name=dire_title, value=format_team(dire), inline=False)
    embed.set_footer(text="🔴 Матч идёт • Обновляется каждую минуту")
    return embed


def build_finished_embed(match_id: int, players_info: list, match_data: dict | None, heroes_list: dict) -> disnake.Embed:
    """Строит embed с итогами завершённого матча с полной статистикой."""
    radiant_win = match_data.get("radiant_win", False) if match_data else False
    duration = match_data.get("duration", 0) if match_data else 0
    radiant_score = match_data.get("radiant_score", 0) if match_data else 0
    dire_score = match_data.get("dire_score", 0) if match_data else 0
    game_mode = match_data.get("game_mode", 0) if match_data else 0
    lobby_type = match_data.get("lobby_type", 0) if match_data else 0
    mode_name = GAME_MODES.get(game_mode, f"Mode {game_mode}")
    lobby_name = LOBBY_TYPES.get(lobby_type, "")

    # Определяем победил ли хоть один привязанный игрок
    linked_players = [p for p in players_info if p.get("linked")]
    if linked_players:
        first_linked_slot = linked_players[0].get("slot", 0)
        linked_is_radiant = first_linked_slot < 128
        linked_won = (radiant_win and linked_is_radiant) or (not radiant_win and not linked_is_radiant)
        color = disnake.Color.green() if linked_won else disnake.Color.red()
    else:
        color = disnake.Color.green() if radiant_win else disnake.Color.red()

    winner = "🟢 Силы Света" if radiant_win else "🔴 Силы Тьмы"
    embed = disnake.Embed(
        title=f"{'🟢' if radiant_win else '🔴'} Матч #{match_id} завершён — {winner}",
        description=f"**{mode_name}**" + (f" • {lobby_name}" if lobby_name else ""),
        color=color,
        url=f"https://www.dotabuff.com/matches/{match_id}"
    )
    embed.add_field(name="Длительность", value=f"{duration // 60}м {duration % 60}с", inline=True)
    embed.add_field(name="Счёт", value=f"🟢 {radiant_score} — {dire_score} 🔴", inline=True)

    # Берём статистику игроков из match_data если есть
    match_players_map = {}
    if match_data:
        for mp in match_data.get("players", []):
            acc_id = mp.get("account_id")
            if acc_id:
                match_players_map[acc_id] = mp

    # Строим словарь steam32 -> players_info для быстрого поиска
    linked_map = {p.get("steam32"): p for p in players_info if p.get("steam32")}

    def format_team_final(slots: range) -> str:
        lines = []
        if not match_data:
            return "—"
        for mp in match_data.get("players", []):
            slot = mp.get("player_slot", 0)
            if slot not in slots:
                continue
            acc_id = mp.get("account_id")
            hero = heroes_list.get(str(mp.get("hero_id", 0)), "—")
            k = mp.get("kills", 0)
            d = mp.get("deaths", 0)
            a = mp.get("assists", 0)
            net = mp.get("net_worth", 0)
            gpm = mp.get("gold_per_min", 0)
            xpm = mp.get("xp_per_min", 0)
            lh = mp.get("last_hits", 0)
            dmg = mp.get("hero_damage", 0)

            # Ищем Discord привязку
            linked_p = linked_map.get(acc_id)
            if linked_p:
                mention = linked_p["mention"]
                line = (
                    f"**{mention}** — {hero}\n"
                    f"└ KDA: {k}/{d}/{a} | Нетворс: {net:,} | GPM: {gpm} | XPM: {xpm}\n"
                    f"└ LH: {lh} | Урон: {dmg:,}"
                )
            else:
                s64 = str(acc_id + 76561197960265728) if acc_id else None
                if s64:
                    # Ищем имя из players_info
                    name_p = next((p for p in players_info if p.get("slot") == slot), None)
                    mention = name_p["mention"] if name_p else f"[Игрок](https://steamcommunity.com/profiles/{s64})"
                else:
                    mention = "Anonymous"
                line = f"{mention} — {hero} | {k}/{d}/{a} | {net:,} нетворс"
            lines.append(line)
        return "\n".join(lines) if lines else "—"

    radiant_slots = range(0, 5)    # player_slot 0-4
    dire_slots = range(128, 133)   # player_slot 128-132

    embed.add_field(name="🟢 Силы Света", value=format_team_final(radiant_slots), inline=False)
    embed.add_field(name="🔴 Силы Тьмы", value=format_team_final(dire_slots), inline=False)
    embed.set_footer(text=f"Подробнее: dotabuff.com/matches/{match_id}")
    return embed


class Dota(commands.Cog):
    def __init__(self, bot: commands.InteractionBot):
        self.bot = bot
        self.track_task = bot.loop.create_task(self.match_tracker_loop())

    def cog_unload(self):
        self.track_task.cancel()

    # --- Трекинг матчей ---

    async def match_tracker_loop(self):
        """Фоновый цикл — проверяет активные матчи каждые 2 минуты."""
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                await self.check_all_guilds()
            except Exception as e:
                log.error(f"Ошибка в match_tracker_loop: {e}")
            await asyncio.sleep(CHECK_INTERVAL)

    async def check_all_guilds(self):
        async with aiohttp.ClientSession() as session:
            heroes_list = await get_heroes_list(session)
            for guild in self.bot.guilds:
                channel_id = _get_server(guild.id, "dota_channel_id")
                if not channel_id:
                    continue
                channel = self.bot.get_channel(channel_id)
                if not channel:
                    continue
                await self.check_guild_matches(session, guild, channel, heroes_list)

    async def check_guild_matches(self, session, guild: disnake.Guild, channel, heroes_list: dict):
        links = get_all_steam_links(guild.id)
        if not links:
            return

        if guild.id not in active_matches:
            active_matches[guild.id] = {}
        if guild.id not in live_messages:
            live_messages[guild.id] = {}

        for user_id, steam64 in links:
            steam32 = steam64_to_32(int(steam64))
            member = guild.get_member(user_id)

            # Получаем последний матч
            try:
                async with session.get(
                    f"{OPENDOTA_API}/players/{steam32}/recentMatches",
                    params={"limit": 1}
                ) as r:
                    if r.status != 200:
                        continue
                    recent = await r.json()
                    if not recent:
                        continue
                    last_match = recent[0]
                    match_id = last_match.get("match_id")
                    if not match_id:
                        continue
            except Exception:
                continue

            # Новый матч — проверяем что он не слишком старый (не старше 2 часов)
            prev_match_id = active_matches[guild.id].get(steam32)
            match_start = last_match.get("start_time", 0)
            match_duration = last_match.get("duration", 0)
            match_end = match_start + match_duration
            now_ts = _time.time()
            is_recent = (now_ts - match_end) < 7200  # не старше 2 часов

            if match_id != prev_match_id and is_recent:
                active_matches[guild.id][steam32] = match_id

                # Пропускаем если этот матч уже обработан другим игроком
                if match_id in live_messages.get(guild.id, {}):
                    continue

                # Если матч ещё идёт (duration == 0 или start_time близко к now)
                is_live = match_duration == 0 or (now_ts - match_start) < match_duration + 300

                # Получаем список всех игроков матча
                try:
                    async with session.get(f"{OPENDOTA_API}/matches/{match_id}") as mr:
                        if mr.status != 200:
                            continue
                        match_data = await mr.json()
                        match_players = match_data.get("players", [])
                        match_steam32_set = {p.get("account_id") for p in match_players if p.get("account_id")}
                except Exception:
                    continue

                # Счёт убийств из данных матча
                radiant_score = match_data.get("radiant_score")
                dire_score = match_data.get("dire_score")
                game_mode = match_data.get("game_mode", 0)
                lobby_type = match_data.get("lobby_type", 0)

                # Строим словарь steam32 -> Discord привязка для быстрого поиска
                linked_map = {steam64_to_32(int(s64)): (uid, s64) for uid, s64 in links}

                # Собираем все steam64 непривязанных игроков для батчевого запроса
                unlinked_steam64s = []
                unlinked_s32s = []
                for mp in match_players:
                    s32 = mp.get("account_id")
                    if s32 and s32 not in linked_map:
                        unlinked_steam64s.append(str(s32 + 76561197960265728))
                        unlinked_s32s.append(s32)

                # Один запрос для всех профилей Steam
                steam_profiles = await get_steam_profiles_batch(session, unlinked_steam64s)

                # Параллельно получаем WR и KDA для непривязанных
                unlinked_stats = {}
                for s32 in unlinked_s32s:
                    w, l = await get_player_wl(session, s32)
                    kda = await get_player_kda(session, s32)
                    top = await get_hero_stats(session, s32)
                    unlinked_stats[s32] = {"wl": (w, l), "kda": kda, "top_heroes": top}

                # Собираем инфу по ВСЕМ 10 игрокам матча
                players_info = []
                for mp in match_players:
                    s32 = mp.get("account_id")
                    hero_id = mp.get("hero_id", 0)
                    slot = mp.get("player_slot", 0)

                    if s32 and s32 in linked_map:
                        uid, s64 = linked_map[s32]
                        m = guild.get_member(uid)
                        w, l = await get_player_wl(session, s32)
                        kda = await get_player_kda(session, s32)
                        top_heroes = await get_hero_stats(session, s32)
                        players_info.append({
                            "mention": m.mention if m else f"ID:{uid}",
                            "hero_id": hero_id,
                            "slot": slot,
                            "steam32": s32,
                            "wl": (w, l),
                            "kda": kda,
                            "top_heroes": top_heroes,
                            "linked": True,
                            "radiant_score": radiant_score, "game_mode": game_mode, "lobby_type": lobby_type,
                            "dire_score": dire_score,
                        })
                    elif s32:
                        s64_other = str(s32 + 76561197960265728)
                        profile = steam_profiles.get(s64_other)
                        if profile:
                            name = profile["personaname"]
                            mention = f"[{name}](https://steamcommunity.com/profiles/{s64_other})"
                        else:
                            mention = f"[Anonymous](https://steamcommunity.com/profiles/{s64_other})"
                        stats = unlinked_stats.get(s32, {})
                        players_info.append({
                            "mention": mention,
                            "hero_id": hero_id,
                            "slot": slot,
                            "steam32": s32,
                            "wl": stats.get("wl"),
                            "kda": stats.get("kda"),
                            "top_heroes": stats.get("top_heroes", []),
                            "linked": False,
                            "radiant_score": radiant_score, "game_mode": game_mode, "lobby_type": lobby_type,
                            "dire_score": dire_score,
                        })
                    else:
                        players_info.append({
                            "mention": "Anonymous",
                            "hero_id": hero_id,
                            "slot": slot,
                            "steam32": None,
                            "linked": False,
                            "radiant_score": radiant_score, "game_mode": game_mode, "lobby_type": lobby_type,
                            "dire_score": dire_score,
                        })

                if not players_info:
                    continue

                # Если матч уже завершён — сразу показываем итоги
                if not is_live and match_data.get("duration"):
                    embed = build_finished_embed(match_id, players_info, match_data, heroes_list)
                    await channel.send(embed=embed)
                    # Помечаем как обработанный чтобы не дублировать
                    live_messages[guild.id][match_id] = (None, players_info)
                    log.info(f"[{guild.name}] Матч #{match_id} уже завершён, показываем итоги")
                else:
                    embed = build_live_embed(match_id, players_info, heroes_list)
                    msg = await channel.send(embed=embed)
                    live_messages[guild.id][match_id] = (msg, players_info)
                    log.info(f"[{guild.name}] Новый матч #{match_id} обнаружен для {member}")

            # Обновляем live сообщение если матч ещё идёт
            elif match_id in live_messages.get(guild.id, {}):
                msg, players_info = live_messages[guild.id][match_id]
                # Проверяем завершился ли матч
                match_data = await get_match_details(session, match_id)
                if match_data and match_data.get("duration"):
                    # Матч завершён — обновляем финальным embed
                    finished_embed = build_finished_embed(match_id, players_info, match_data, heroes_list)
                    try:
                        await msg.edit(embed=finished_embed)
                    except Exception:
                        pass
                    del live_messages[guild.id][match_id]

                    # Обновляем недельную статистику и проверяем ранг
                    radiant_win = match_data.get("radiant_win", False)
                    week = current_week()
                    for p in players_info:
                        if not p.get("linked") or not p.get("steam32"):
                            continue
                        uid = next((u for u, s64 in links if steam64_to_32(int(s64)) == p["steam32"]), None)
                        if not uid:
                            continue
                        slot = p.get("slot", 0)
                        won = (radiant_win and slot < 128) or (not radiant_win and slot >= 128)
                        add_dota_match(guild.id, uid, won, week)

                        # Проверяем изменение ранга
                        async with aiohttp.ClientSession() as rank_session:
                            s64 = next((s64 for u, s64 in links if u == uid), None)
                            if s64:
                                s32 = steam64_to_32(int(s64))
                                stats = await get_dota_stats(rank_session, s32)
                                if stats:
                                    new_rank = stats.get("rank_tier", 0) or 0
                                    old_row = get_dota_player(guild.id, uid)
                                    old_rank = old_row[0] if old_row else 0
                                    if new_rank and new_rank != old_rank:
                                        update_dota_rank(guild.id, uid, new_rank)
                                        member = guild.get_member(uid)
                                        if member and old_rank:
                                            went_up = new_rank > old_rank
                                            emoji = "📈" if went_up else "📉"
                                            change = "повысил" if went_up else "понизил"
                                            await channel.send(
                                                f"{emoji} {member.mention} **{change}** ранг: "
                                                f"**{rank_name(old_rank)}** → **{rank_name(new_rank)}**"
                                            )
                                            log.info(f"[{guild.name}] {member} ранг: {rank_name(old_rank)} → {rank_name(new_rank)}")

                    log.info(f"[{guild.name}] Матч #{match_id} завершён")
                else:
                    # Матч ещё идёт — обновляем embed с актуальным счётом
                    match_data = await get_match_details(session, match_id)
                    if match_data:
                        rs = match_data.get("radiant_score")
                        ds = match_data.get("dire_score")
                        for p in players_info:
                            if rs is not None:
                                p["radiant_score"] = rs
                            if ds is not None:
                                p["dire_score"] = ds
                    live_embed = build_live_embed(match_id, players_info, heroes_list)
                    try:
                        await msg.edit(embed=live_embed)
                    except Exception:
                        pass

    # --- Команды ---

    @commands.slash_command(
        description="Назначить канал для уведомлений о матчах Dota 2",
        default_member_permissions=disnake.Permissions(administrator=True)
    )
    async def set_dota_channel(self, inter: disnake.ApplicationCommandInteraction, channel: disnake.TextChannel):
        _upsert_server(inter.guild.id, dota_channel_id=channel.id)
        await inter.response.send_message(f"✅ Канал Dota 2 уведомлений: {channel.mention}", ephemeral=True)

    @commands.slash_command(description="Привязать Steam аккаунт")
    async def link_steam(self, inter: disnake.ApplicationCommandInteraction, steam: str):
        await inter.response.defer(ephemeral=True)
        async with aiohttp.ClientSession() as session:
            steam64 = resolve_steam_id(steam)
            if not steam64:
                steam64 = await resolve_vanity(session, steam)
            if not steam64:
                return await inter.edit_original_response(content="❌ Не удалось найти Steam аккаунт.")
            profile = await get_steam_profile(session, steam64)
            if not profile:
                return await inter.edit_original_response(content="❌ Профиль не найден или закрыт.")

        link_steam(inter.guild.id, inter.author.id, steam64)
        log.info(f"[{inter.guild.name}] {inter.author} привязал Steam: {profile['personaname']} ({steam64})")
        embed = disnake.Embed(title="✅ Steam аккаунт привязан", color=disnake.Color.green())
        embed.add_field(name="Ник", value=profile["personaname"], inline=True)
        embed.add_field(name="Steam64 ID", value=steam64, inline=True)
        embed.set_thumbnail(url=profile.get("avatarfull", ""))
        await inter.edit_original_response(embed=embed)

    @commands.slash_command(description="Отвязать Steam аккаунт")
    async def unlink_steam(self, inter: disnake.ApplicationCommandInteraction):
        if not get_steam_id(inter.guild.id, inter.author.id):
            return await inter.response.send_message("❌ Steam аккаунт не привязан.", ephemeral=True)
        unlink_steam(inter.guild.id, inter.author.id)
        await inter.response.send_message("✅ Steam аккаунт отвязан.", ephemeral=True)

    @commands.slash_command(description="Статистика Dota 2")
    async def dota(self, inter: disnake.ApplicationCommandInteraction, member: disnake.Member = None):
        member = member or inter.author
        steam64 = get_steam_id(inter.guild.id, member.id)
        if not steam64:
            return await inter.response.send_message(
                f"❌ {'У тебя' if member == inter.author else f'У {member.mention}'} не привязан Steam. Используй `/link_steam`.",
                ephemeral=True
            )
        await inter.response.defer()
        steam32 = steam64_to_32(int(steam64))

        async with aiohttp.ClientSession() as session:
            profile = await get_steam_profile(session, steam64)
            stats = await get_dota_stats(session, steam32)
            heroes = await get_hero_stats(session, steam32)
            heroes_list = await get_heroes_list(session)

        if not stats:
            return await inter.edit_original_response(content="❌ Не удалось получить статистику. Профиль может быть закрыт.")

        mmr = stats.get("mmr_estimate", {}).get("estimate", "—")
        rank_tier = stats.get("rank_tier")
        rank_names = {1: "Страж", 2: "Страж", 3: "Рыцарь", 4: "Герой", 5: "Легенда", 6: "Властелин", 7: "Властелин", 8: "Бессмертный"}
        rank = rank_names.get(rank_tier // 10 if rank_tier else 0, "—") if rank_tier else "—"

        embed = disnake.Embed(
            title=f"🎮 Dota 2 — {profile['personaname'] if profile else member.display_name}",
            color=disnake.Color.dark_red()
        )
        if profile:
            embed.set_thumbnail(url=profile.get("avatarfull", ""))
        embed.add_field(name="Ранг", value=rank, inline=True)
        embed.add_field(name="MMR (оценка)", value=str(mmr), inline=True)

        if heroes and heroes_list:
            lines = []
            for h in heroes:
                name = heroes_list.get(str(h["hero_id"]), f"ID:{h['hero_id']}")
                games = h.get("games", 0)
                wins = h.get("win", 0)
                wr = round(wins / games * 100) if games else 0
                lines.append(f"**{name}** — {games} игр, {wr}% побед")
            embed.add_field(name="Топ герои", value="\n".join(lines), inline=False)

        embed.set_footer(text=f"Steam64: {steam64} | Данные: OpenDota")
        await inter.edit_original_response(embed=embed)

    @commands.slash_command(description="Статистика матчей гильдии за неделю")
    async def dota_week(self, inter: disnake.ApplicationCommandInteraction):
        rows = get_guild_week_stats(inter.guild.id)
        if not rows:
            return await inter.response.send_message("❌ Нет данных за эту неделю.", ephemeral=True)

        embed = disnake.Embed(
            title="📊 Матчи гильдии за неделю",
            color=disnake.Color.blurple()
        )
        lines = []
        for i, (user_id, matches, wins) in enumerate(rows):
            member = inter.guild.get_member(user_id)
            name = member.display_name if member else f"ID:{user_id}"
            wr = round(wins / matches * 100) if matches else 0
            medals = ["🥇", "🥈", "🥉"]
            prefix = medals[i] if i < 3 else f"{i+1}."
            lines.append(f"{prefix} **{name}** — {matches} матчей, {wins}W/{matches-wins}L ({wr}%)")

        embed.description = "\n".join(lines)
        embed.set_footer(text=f"Неделя с {current_week()}")
        await inter.response.send_message(embed=embed)

    @commands.slash_command(description="Последние матчи в Dota 2")
    async def dota_matches(self, inter: disnake.ApplicationCommandInteraction, member: disnake.Member = None):
        member = member or inter.author
        steam64 = get_steam_id(inter.guild.id, member.id)
        if not steam64:
            return await inter.response.send_message("❌ Steam аккаунт не привязан.", ephemeral=True)

        await inter.response.defer()
        steam32 = steam64_to_32(int(steam64))

        async with aiohttp.ClientSession() as session:
            matches = await get_recent_matches(session, steam32)
            heroes_list = await get_heroes_list(session)

        if not matches:
            return await inter.edit_original_response(content="❌ Матчи не найдены или профиль закрыт.")

        embed = disnake.Embed(title=f"⚔️ Последние матчи — {member.display_name}", color=disnake.Color.dark_red())
        for m in matches:
            hero = heroes_list.get(str(m.get("hero_id", 0)), "Неизвестный герой")
            won = m.get("radiant_win") == (m.get("player_slot", 0) < 128)
            result = "✅ Победа" if won else "❌ Поражение"
            duration = f"{m.get('duration', 0) // 60}м"
            kda = f"{m.get('kills', 0)}/{m.get('deaths', 0)}/{m.get('assists', 0)}"
            embed.add_field(name=f"{hero} — {result}", value=f"KDA: {kda} | {duration}", inline=False)

        embed.set_footer(text="Данные: OpenDota")
        await inter.edit_original_response(embed=embed)


def setup(bot: commands.InteractionBot):
    bot.add_cog(Dota(bot))
