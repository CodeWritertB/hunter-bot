import os
import asyncio
import logging
import aiohttp
import disnake
from disnake.ext import commands
from db.database import get_all_cs_links, get_steam_id, _upsert_server, _get_server, link_faceit, get_faceit_id

log = logging.getLogger("cogs.cs2")

STEAM_API_KEY = os.getenv("STEAM_API_KEY")
FACEIT_API_KEY = os.getenv("FACEIT_API_KEY")

STEAM_API = "https://api.steampowered.com"
FACEIT_API = "https://open.faceit.com/data/v4"
CS2_APP_ID = 730

CHECK_INTERVAL = 60  # Проверка активных матчей каждые 60 секунд

# guild_id -> {steam32 -> match_id}
active_cs_matches: dict[int, dict[str, str]] = {}
# guild_id -> {match_id -> (message, players_info)}
live_cs_messages: dict[int, dict[str, tuple]] = {}


# --- Steam API ---

async def get_steam_profile(session: aiohttp.ClientSession, steam64: str) -> dict | None:
    url = f"{STEAM_API}/ISteamUser/GetPlayerSummaries/v2/"
    async with session.get(url, params={"key": STEAM_API_KEY, "steamids": steam64}) as r:
        data = await r.json()
        players = data.get("response", {}).get("players", [])
        return players[0] if players else None


async def get_cs2_stats(session: aiohttp.ClientSession, steam64: str) -> dict | None:
    """Получает статистику CS2 через Steam API."""
    url = f"{STEAM_API}/ISteamUserStats/GetUserStatsForGame/v2/"
    async with session.get(url, params={"key": STEAM_API_KEY, "steamid": steam64, "appid": CS2_APP_ID}) as r:
        if r.status != 200:
            return None
        data = await r.json()
        stats_list = data.get("playerstats", {}).get("stats", [])
        return {s["name"]: s["value"] for s in stats_list}


# --- Faceit API ---

FACEIT_HEADERS = lambda: {"Authorization": f"Bearer {FACEIT_API_KEY}"}


async def get_faceit_player(session: aiohttp.ClientSession, steam64: str) -> dict | None:
    """Ищет Faceit профиль по Steam ID."""
    async with session.get(
        f"{FACEIT_API}/players",
        params={"game": "cs2", "game_player_id": steam64},
        headers=FACEIT_HEADERS()
    ) as r:
        if r.status != 200:
            return None
        return await r.json()


async def get_faceit_stats(session: aiohttp.ClientSession, faceit_id: str) -> dict | None:
    """Получает статистику Faceit игрока по CS2."""
    async with session.get(
        f"{FACEIT_API}/players/{faceit_id}/stats/cs2",
        headers=FACEIT_HEADERS()
    ) as r:
        if r.status != 200:
            return None
        return await r.json()


async def get_faceit_match(session: aiohttp.ClientSession, faceit_id: str) -> dict | None:
    """Проверяет активный матч игрока на Faceit."""
    async with session.get(
        f"{FACEIT_API}/players/{faceit_id}/history",
        params={"game": "cs2", "limit": 1},
        headers=FACEIT_HEADERS()
    ) as r:
        if r.status != 200:
            return None
        data = await r.json()
        items = data.get("items", [])
        return items[0] if items else None


async def get_faceit_match_details(session: aiohttp.ClientSession, match_id: str) -> dict | None:
    """Получает детали матча Faceit."""
    async with session.get(
        f"{FACEIT_API}/matches/{match_id}",
        headers=FACEIT_HEADERS()
    ) as r:
        if r.status != 200:
            return None
        return await r.json()


# --- Embed builders ---

def build_cs_live_embed(match_id: str, players_info: list, match_data: dict | None) -> disnake.Embed:
    embed = disnake.Embed(
        title=f"🔫 Активный матч CS2",
        color=disnake.Color.orange(),
        url=f"https://www.faceit.com/en/cs2/room/{match_id}" if match_id else None
    )
    if match_data:
        status = match_data.get("status", "—")
        embed.add_field(name="Статус", value=status, inline=True)
        teams = match_data.get("teams", {})
        t1 = teams.get("faction1", {}).get("name", "Команда 1")
        t2 = teams.get("faction2", {}).get("name", "Команда 2")
        embed.add_field(name="Команды", value=f"{t1} vs {t2}", inline=True)

    linked = [p for p in players_info if p.get("linked")]
    others = [p for p in players_info if not p.get("linked")]

    # Привязанные — полная карточка
    for p in linked:
        elo = p.get("elo", "—")
        level = p.get("level", "—")
        wr = p.get("winrate", "—")
        kd = p.get("kd", "—")
        hs = p.get("hs", "—")
        value = (
            f"Faceit ELO: **{elo}** (уровень {level})\n"
            f"Винрейт: **{wr}%**\n"
            f"K/D: **{kd}** | HS: **{hs}%**"
        )
        embed.add_field(name=p["mention"], value=value, inline=True)

    # Остальные — только ник
    if others:
        lines = [p["mention"] for p in others]
        embed.add_field(name="Остальные игроки", value="\n".join(lines), inline=False)

    embed.set_footer(text="🔴 Матч идёт • Обновляется каждую минуту")
    return embed


def build_cs_finished_embed(match_id: str, players_info: list, match_data: dict | None) -> disnake.Embed:
    embed = disnake.Embed(
        title="✅ Матч CS2 завершён",
        color=disnake.Color.green(),
        url=f"https://www.faceit.com/en/cs2/room/{match_id}" if match_id else None
    )
    if match_data:
        results = match_data.get("results", {})
        winner = results.get("winner", "—")
        embed.add_field(name="Победитель", value=winner, inline=True)

    if players_info:
        linked = [p for p in players_info if p.get("linked")]
        lines = [f"{p['mention']} — ELO: {p.get('elo', '—')}" for p in linked]
        if lines:
            embed.add_field(name="Игроки сервера", value="\n".join(lines), inline=False)

    embed.set_footer(text=f"Faceit: faceit.com/en/cs2/room/{match_id}")
    return embed


class CS2(commands.Cog):
    def __init__(self, bot: commands.InteractionBot):
        self.bot = bot
        self.track_task = bot.loop.create_task(self.match_tracker_loop())

    def cog_unload(self):
        self.track_task.cancel()

    # --- Трекинг ---

    async def match_tracker_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                await self.check_all_guilds()
            except Exception as e:
                log.error(f"Ошибка в cs2 match_tracker_loop: {e}")
            await asyncio.sleep(CHECK_INTERVAL)

    async def check_all_guilds(self):
        async with aiohttp.ClientSession() as session:
            for guild in self.bot.guilds:
                channel_id = _get_server(guild.id, "cs_channel_id")
                if not channel_id:
                    continue
                channel = self.bot.get_channel(channel_id)
                if not channel:
                    continue
                await self.check_guild_matches(session, guild, channel)

    async def check_guild_matches(self, session, guild: disnake.Guild, channel):
        links = get_all_cs_links(guild.id)
        if not links:
            return

        if guild.id not in active_cs_matches:
            active_cs_matches[guild.id] = {}
        if guild.id not in live_cs_messages:
            live_cs_messages[guild.id] = {}

        for user_id, steam64, faceit_id in links:
            if not faceit_id:
                continue

            # Получаем последний матч через Faceit
            try:
                last = await get_faceit_match(session, faceit_id)
                if not last:
                    continue
                match_id = last.get("match_id")
                if not match_id:
                    continue
            except Exception:
                continue

            prev = active_cs_matches[guild.id].get(faceit_id)

            if match_id != prev:
                active_cs_matches[guild.id][faceit_id] = match_id

                # Детали матча
                match_data = await get_faceit_match_details(session, match_id)
                if not match_data:
                    continue

                # Все игроки матча
                all_players = []
                teams = match_data.get("teams", {})
                for team in teams.values():
                    all_players.extend(team.get("roster", []))

                # Строим словарь faceit_id -> Discord привязка
                linked_map = {fid: (uid, s64) for uid, s64, fid in links if fid}

                players_info = []
                for mp in all_players:
                    p_faceit_id = mp.get("player_id")
                    p_nickname = mp.get("nickname", "Unknown")

                    if p_faceit_id and p_faceit_id in linked_map:
                        uid, s64 = linked_map[p_faceit_id]
                        m = guild.get_member(uid)
                        # Получаем Faceit статистику
                        fstats = await get_faceit_stats(session, p_faceit_id)
                        fplayer = await get_faceit_player(session, s64)
                        elo = fplayer.get("games", {}).get("cs2", {}).get("faceit_elo", "—") if fplayer else "—"
                        level = fplayer.get("games", {}).get("cs2", {}).get("skill_level", "—") if fplayer else "—"
                        lifetime = fstats.get("lifetime", {}) if fstats else {}
                        wr = lifetime.get("Win Rate %", "—")
                        kd = lifetime.get("Average K/D Ratio", "—")
                        hs = lifetime.get("Average Headshots %", "—")
                        players_info.append({
                            "mention": m.mention if m else f"ID:{uid}",
                            "elo": elo, "level": level,
                            "winrate": wr, "kd": kd, "hs": hs,
                            "linked": True,
                        })
                    else:
                        # Не привязан — показываем ник ссылкой
                        if p_faceit_id:
                            mention = f"[{p_nickname}](https://www.faceit.com/en/players/{p_nickname})"
                        else:
                            mention = "Anonymous"
                        players_info.append({"mention": mention, "linked": False})

                embed = build_cs_live_embed(match_id, players_info, match_data)
                msg = await channel.send(embed=embed)
                live_cs_messages[guild.id][match_id] = (msg, players_info)
                log.info(f"[{guild.name}] CS2 матч {match_id} обнаружен")

            elif match_id in live_cs_messages.get(guild.id, {}):
                msg, players_info = live_cs_messages[guild.id][match_id]
                match_data = await get_faceit_match_details(session, match_id)
                if match_data and match_data.get("status") == "FINISHED":
                    finished = build_cs_finished_embed(match_id, players_info, match_data)
                    try:
                        await msg.edit(embed=finished)
                    except Exception:
                        pass
                    del live_cs_messages[guild.id][match_id]
                    log.info(f"[{guild.name}] CS2 матч {match_id} завершён")
                else:
                    live_embed = build_cs_live_embed(match_id, players_info, match_data)
                    try:
                        await msg.edit(embed=live_embed)
                    except Exception:
                        pass

    # --- Команды ---

    @commands.slash_command(
        description="Назначить канал для уведомлений о матчах CS2",
        default_member_permissions=disnake.Permissions(administrator=True)
    )
    async def set_cs_channel(self, inter: disnake.ApplicationCommandInteraction, channel: disnake.TextChannel):
        _upsert_server(inter.guild.id, cs_channel_id=channel.id)
        await inter.response.send_message(f"✅ Канал CS2 уведомлений: {channel.mention}", ephemeral=True)

    @commands.slash_command(description="Привязать Faceit аккаунт для CS2")
    async def link_cs(self, inter: disnake.ApplicationCommandInteraction, faceit_nickname: str):
        """Привязывает Faceit аккаунт по нику."""
        await inter.response.defer(ephemeral=True)

        async with aiohttp.ClientSession() as session:
            # Ищем игрока по нику
            async with session.get(
                f"{FACEIT_API}/players",
                params={"nickname": faceit_nickname},
                headers=FACEIT_HEADERS()
            ) as r:
                if r.status != 200:
                    return await inter.edit_original_response(content="❌ Faceit аккаунт не найден.")
                player = await r.json()

            faceit_id = player.get("player_id")
            steam64 = player.get("games", {}).get("cs2", {}).get("game_player_id")
            if not faceit_id or not steam64:
                return await inter.edit_original_response(content="❌ CS2 не найден в аккаунте Faceit.")

            elo = player.get("games", {}).get("cs2", {}).get("faceit_elo", "—")
            level = player.get("games", {}).get("cs2", {}).get("skill_level", "—")

        link_faceit(inter.guild.id, inter.author.id, faceit_id, steam64)
        log.info(f"[{inter.guild.name}] {inter.author} привязал Faceit: {faceit_nickname}")

        embed = disnake.Embed(title="✅ Faceit аккаунт привязан", color=disnake.Color.orange())
        embed.add_field(name="Ник", value=faceit_nickname, inline=True)
        embed.add_field(name="ELO", value=str(elo), inline=True)
        embed.add_field(name="Уровень", value=str(level), inline=True)
        embed.set_thumbnail(url=player.get("avatar", ""))
        await inter.edit_original_response(embed=embed)

    @commands.slash_command(description="Статистика CS2 / Faceit")
    async def cs(self, inter: disnake.ApplicationCommandInteraction, member: disnake.Member = None):
        member = member or inter.author
        faceit_id = get_faceit_id(inter.guild.id, member.id)
        steam64 = get_steam_id(inter.guild.id, member.id)

        if not faceit_id and not steam64:
            return await inter.response.send_message(
                "❌ Аккаунт не привязан. Используй `/link_cs`.", ephemeral=True
            )

        await inter.response.defer()

        async with aiohttp.ClientSession() as session:
            embed = disnake.Embed(title=f"🔫 CS2 — {member.display_name}", color=disnake.Color.orange())

            # Faceit статистика
            if faceit_id:
                fplayer = await get_faceit_player(session, steam64) if steam64 else None
                fstats = await get_faceit_stats(session, faceit_id)

                if fplayer:
                    elo = fplayer.get("games", {}).get("cs2", {}).get("faceit_elo", "—")
                    level = fplayer.get("games", {}).get("cs2", {}).get("skill_level", "—")
                    embed.add_field(name="Faceit ELO", value=str(elo), inline=True)
                    embed.add_field(name="Уровень", value=str(level), inline=True)
                    embed.set_thumbnail(url=fplayer.get("avatar", ""))

                if fstats:
                    lifetime = fstats.get("lifetime", {})
                    embed.add_field(name="Матчей", value=lifetime.get("Matches", "—"), inline=True)
                    embed.add_field(name="Винрейт", value=f"{lifetime.get('Win Rate %', '—')}%", inline=True)
                    embed.add_field(name="K/D", value=lifetime.get("Average K/D Ratio", "—"), inline=True)
                    embed.add_field(name="HS%", value=f"{lifetime.get('Average Headshots %', '—')}%", inline=True)
                    embed.add_field(name="Убийств/матч", value=lifetime.get("Average Kills", "—"), inline=True)

            # Steam статистика CS2
            if steam64:
                cs_stats = await get_cs2_stats(session, steam64)
                if cs_stats:
                    kills = cs_stats.get("total_kills", 0)
                    deaths = cs_stats.get("total_deaths", 0)
                    wins = cs_stats.get("total_wins", 0)
                    hs = cs_stats.get("total_kills_headshot", 0)
                    embed.add_field(name="Всего убийств", value=str(kills), inline=True)
                    embed.add_field(name="Всего побед", value=str(wins), inline=True)
                    embed.add_field(name="HS всего", value=str(hs), inline=True)

            embed.set_footer(text="Данные: Faceit API + Steam")
            await inter.edit_original_response(embed=embed)


def setup(bot: commands.InteractionBot):
    bot.add_cog(CS2(bot))
