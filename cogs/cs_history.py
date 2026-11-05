"""Valve sharing-code history. The Web API provides codes, not match statistics."""
import asyncio
import os
import re
import time
import logging
import aiohttp
import disnake
from disnake.ext import commands
from db.database import conn, _get_server

log = logging.getLogger(__name__)
CODE = re.compile(r"CSGO-(?:[A-Za-z0-9]{5}-){4}[A-Za-z0-9]{5}")
_last_poll = 0


def tables():
    conn.execute("CREATE TABLE IF NOT EXISTS cs_history_links (guild_id INTEGER, user_id INTEGER, steam_id TEXT, auth_code TEXT, cursor TEXT, PRIMARY KEY(guild_id,user_id))")
    conn.execute("CREATE TABLE IF NOT EXISTS cs_history_posts (guild_id INTEGER, code TEXT, PRIMARY KEY(guild_id,code))")
    conn.commit()


async def next_code(session, steam, auth, cursor):
    params = {"key": os.getenv("STEAM_API_KEY", ""), "steamid": steam, "steamidkey": auth, "knowncode": cursor}
    async with session.get("https://api.steampowered.com/ICSGOPlayers_730/GetNextMatchSharingCode/v1/", params=params) as response:
        if response.status == 202:
            return None
        if response.status != 200:
            raise ValueError(f"Steam отклонил проверку (HTTP {response.status}). Проверь оба кода и Steam ID.")
        value = (await response.json()).get("result", {}).get("nextcode")
        return value if value and CODE.fullmatch(value) and value != cursor else None


class HistoryModal(disnake.ui.Modal):
    def __init__(self):
        super().__init__(title="Подключить матчи CS2", components=[
            disnake.ui.TextInput(label="Steam ID64 (17 цифр)", custom_id="steam", max_length=17),
            disnake.ui.TextInput(label="Код доступа к истории", custom_id="auth", placeholder="Код с личной страницы Steam", max_length=40),
            disnake.ui.TextInput(label="Код последнего матча", custom_id="code", placeholder="CSGO-xxxxx-xxxxx-xxxxx-xxxxx-xxxxx", max_length=34)])

    async def callback(self, inter):
        steam, auth, code = (inter.text_values[key].strip() for key in ("steam","auth","code"))
        if not re.fullmatch(r"7656119\d{10}", steam) or not CODE.fullmatch(code) or not re.fullmatch(r"[A-Za-z0-9-]{10,40}", auth):
            return await inter.response.send_message("Проверь формат полей. Steam ID64 содержит 17 цифр, код матча начинается с CSGO-.", ephemeral=True)
        if not os.getenv("STEAM_API_KEY"):
            return await inter.response.send_message("Администратору нужно настроить STEAM_API_KEY.", ephemeral=True)
        await inter.response.defer(ephemeral=True)
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
                await next_code(session, steam, auth, code)
        except (ValueError, aiohttp.ClientError, TimeoutError):
            return await inter.edit_original_response(content="Steam не подтвердил подключение. Проверь ID и коды или попробуй позже.")
        tables()
        conn.execute("INSERT OR REPLACE INTO cs_history_links VALUES (?,?,?,?,?)",(inter.guild.id,inter.author.id,steam,auth,code))
        conn.commit()
        await inter.edit_original_response(content="История подключена. Проверка новых кодов выполняется раз в пять минут. Сейчас доступны уведомления о новых матчах и их share-коды. Счёт и полная статистика требуют отдельного получения демо.")


class HistoryHelp(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(disnake.ui.Button(label="Получить код доступа в Steam", url="https://help.steampowered.com/en/wizard/HelpWithGameIssue/?appid=730&issueid=128"))
        self.add_item(disnake.ui.Button(label="Открыть историю матчей", url="https://steamcommunity.com/my/gcpd/730/?tab=matchhistorycompetitive"))

    @disnake.ui.button(label="Вставить коды", style=disnake.ButtonStyle.primary)
    async def enter(self, button, inter):
        await inter.response.send_modal(HistoryModal())

    @disnake.ui.button(label="Отключить историю", style=disnake.ButtonStyle.danger)
    async def disconnect(self, button, inter):
        tables()
        conn.execute("DELETE FROM cs_history_links WHERE guild_id=? AND user_id=?", (inter.guild.id,inter.author.id))
        conn.commit()
        await inter.response.send_message("История отключена, код доступа удалён из базы.",ephemeral=True)


async def show_help(inter):
    await inter.response.send_message(
        "Подключение обычных матчей CS2\n"
        "1. Открой личную страницу Steam кнопкой ниже. Найди код доступа к истории матчей (Game Authentication Code).\n"
        "2. Скопируй код последнего завершённого матча вида CSGO-… из Steam или CS2 → «Смотреть» → «Ваши матчи».\n"
        "3. Нажми «Вставить коды» и заполни три поля. Steam ID64 указан в профиле Steam.\n"
        "Код доступа хранится в базе бота для проверки новых матчей. Его можно отозвать в Steam или удалить кнопкой ниже.\n"
        "Первый этап отслеживает появление новых матчей. Подробный счёт и статистика через демо ещё не подключены.",
        view=HistoryHelp(), ephemeral=True)


async def poll_history(bot):
    global _last_poll
    if not os.getenv("STEAM_API_KEY") or time.monotonic()-_last_poll < 300:
        return
    _last_poll = time.monotonic()
    tables()
    rows=conn.execute("SELECT guild_id,user_id,steam_id,auth_code,cursor FROM cs_history_links").fetchall()
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        for gid, uid, steam, auth, cursor in rows:
            guild=bot.get_guild(gid)
            channel=guild.get_channel(_get_server(gid,"cs_channel_id") or 0) if guild else None
            if not channel:
                continue
            try:
                for _ in range(5):
                    code=await next_code(session,steam,auth,cursor)
                    if not code:
                        break
                    posted=conn.execute("SELECT 1 FROM cs_history_posts WHERE guild_id=? AND code=?",(gid,code)).fetchone()
                    if not posted:
                        member=guild.get_member(uid)
                        embed=disnake.Embed(title="Обнаружен завершённый матч Valve")
                        embed.add_field(name="Игрок",value=member.display_name if member else "Участник")
                        embed.add_field(name="Share-код",value=code)
                        embed.add_field(name="Статистика",value="Ожидает подключения обработки демо")
                        from cogs.cs_card import render_cs_card
                        buffer=await asyncio.to_thread(render_cs_card,embed,guild,{"source":"valve"},[])
                        await channel.send(content=f"Новый матч CS2 · \x60{code}\x60",file=disnake.File(buffer,filename="cs2-valve.png"),allowed_mentions=disnake.AllowedMentions.none())
                        conn.execute("INSERT OR IGNORE INTO cs_history_posts VALUES (?,?)",(gid,code))
                    conn.execute("UPDATE cs_history_links SET cursor=? WHERE guild_id=? AND user_id=? AND cursor=?",(code,gid,uid,cursor))
                    conn.commit()
                    cursor=code
            except (aiohttp.ClientError, TimeoutError, ValueError, disnake.HTTPException):
                log.warning("Не удалось обновить историю CS2 для guild=%s user=%s",gid,uid)
