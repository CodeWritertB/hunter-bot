import asyncio
import json
import logging
import aiohttp
import disnake
from disnake.ext import commands
from db.database import get_music_channel, set_music_channel as db_set_music_channel

log = logging.getLogger("cogs.music")

LAVALINK_HOST = "lava-v4.ajieblogs.eu.org"
LAVALINK_PORT = 80
LAVALINK_PASSWORD = "https://dsc.gg/ajidevserver"
LAVALINK_SECURE = False

LAVALINK_BASE = f"{'https' if LAVALINK_SECURE else 'http'}://{LAVALINK_HOST}:{LAVALINK_PORT}"
LAVALINK_HEADERS = {"Authorization": LAVALINK_PASSWORD, "Content-Type": "application/json"}

music_cog_session: str | None = None


def fmt_time(ms: int) -> str:
    """Форматирует миллисекунды в MM:SS."""
    s = ms // 1000
    return f"{s // 60}:{s % 60:02d}"


def progress_bar(position: int, duration: int, length: int = 12) -> str:
    """Строит текстовый прогресс-бар."""
    if not duration:
        return "─" * length
    filled = int(position / duration * length)
    return "▬" * filled + "🔘" + "─" * (length - filled)


class MusicPlayer:
    def __init__(self):
        self.queue: list[dict] = []
        self.current: dict | None = None
        self.message: disnake.Message | None = None
        self.paused: bool = False
        self.position: int = 0       # текущая позиция в мс
        self.session_id: str | None = None


players: dict[int, MusicPlayer] = {}


def get_player(guild_id: int) -> MusicPlayer:
    if guild_id not in players:
        players[guild_id] = MusicPlayer()
    return players[guild_id]


async def lavalink_request(method: str, path: str, **kwargs):
    async with aiohttp.ClientSession() as session:
        async with session.request(method, f"{LAVALINK_BASE}{path}", headers=LAVALINK_HEADERS, **kwargs) as r:
            try:
                return await r.json() if r.status in (200, 204) else None
            except Exception:
                return None


async def search_track(query: str) -> dict | None:
    if not query.startswith("http"):
        query = f"ytsearch:{query}"
    data = await lavalink_request("GET", "/v4/loadtracks", params={"identifier": query})
    if not data:
        return None
    lt = data.get("loadType")
    if lt == "track":
        return data["data"]
    elif lt == "search":
        tracks = data.get("data", [])
        return tracks[0] if tracks else None
    return None


def build_embed(player: MusicPlayer) -> disnake.Embed:
    embed = disnake.Embed(title="🎵 Музыкальный плеер", color=disnake.Color.blurple())
    if player.current:
        info = player.current.get("info", {})
        title = info.get("title", "Неизвестно")
        uri = info.get("uri", "")
        duration = info.get("length", 0)
        pos = player.position

        bar = progress_bar(pos, duration)
        time_str = f"`{fmt_time(pos)} {bar} {fmt_time(duration)}`"
        status = "⏸ Пауза" if player.paused else "▶️ Играет"

        embed.add_field(
            name=f"{status} — [{title}]({uri})" if uri else f"{status} — {title}",
            value=time_str,
            inline=False
        )
    else:
        embed.add_field(name="Сейчас играет", value="Ничего", inline=False)

    if player.queue:
        lines = [f"{i+1}. {t.get('info', {}).get('title', '?')}" for i, t in enumerate(player.queue[:5])]
        if len(player.queue) > 5:
            lines.append(f"...и ещё {len(player.queue) - 5}")
        embed.add_field(name="Очередь", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="Очередь", value="Пусто", inline=False)
    return embed


class MusicView(disnake.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self._update_pause_button()

    def _update_pause_button(self):
        """Обновляет кнопку паузы в зависимости от состояния."""
        player = players.get(self.guild_id)
        paused = player.paused if player else False
        # Находим кнопку паузы и меняем emoji
        for item in self.children:
            if hasattr(item, "custom_id") and item.custom_id == "pause_toggle":
                item.emoji = "▶️" if paused else "⏸"
                item.style = disnake.ButtonStyle.success if paused else disnake.ButtonStyle.secondary
                break

    @disnake.ui.button(emoji="⏸", style=disnake.ButtonStyle.secondary, custom_id="pause_toggle")
    async def pause_toggle(self, button, inter: disnake.MessageInteraction):
        player = get_player(self.guild_id)
        player.paused = not player.paused
        await lavalink_request(
            "PATCH", f"/v4/sessions/{music_cog_session}/players/{self.guild_id}",
            json={"paused": player.paused}
        )
        self._update_pause_button()
        await inter.response.edit_message(embed=build_embed(player), view=self)

    @disnake.ui.button(emoji="⏭", style=disnake.ButtonStyle.secondary)
    async def skip_btn(self, button, inter: disnake.MessageInteraction):
        player = get_player(self.guild_id)
        if player.queue:
            player.current = player.queue.pop(0)
            player.position = 0
            await lavalink_request(
                "PATCH", f"/v4/sessions/{music_cog_session}/players/{self.guild_id}",
                json={"track": {"encoded": player.current.get("encoded")}}
            )
        else:
            player.current = None
        await inter.response.edit_message(embed=build_embed(player), view=self)

    @disnake.ui.button(emoji="⏹", style=disnake.ButtonStyle.danger)
    async def stop_btn(self, button, inter: disnake.MessageInteraction):
        player = get_player(self.guild_id)
        player.queue.clear()
        player.current = None
        player.position = 0
        await lavalink_request("DELETE", f"/v4/sessions/{music_cog_session}/players/{self.guild_id}")
        await inter.guild._state.ws.voice_state(self.guild_id, None)
        await inter.response.edit_message(embed=build_embed(player), view=None)


class Music(commands.Cog):
    def __init__(self, bot: commands.InteractionBot):
        self.bot = bot
        self.session_id: str | None = None
        self.ws_task = bot.loop.create_task(self.connect_lavalink())
        self.update_task = bot.loop.create_task(self.progress_update_loop())

    def cog_unload(self):
        self.ws_task.cancel()
        self.update_task.cancel()

    async def progress_update_loop(self):
        """Обновляет прогресс-бар каждые 5 секунд."""
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            await asyncio.sleep(5)
            if not self.session_id:
                continue
            for guild_id, player in list(players.items()):
                if not player.current or not player.message or player.paused:
                    continue
                try:
                    # Получаем актуальную позицию из Lavalink
                    data = await lavalink_request("GET", f"/v4/sessions/{self.session_id}/players/{guild_id}")
                    if data:
                        player.position = data.get("state", {}).get("position", player.position)
                    view = MusicView(guild_id)
                    await player.message.edit(embed=build_embed(player), view=view)
                except Exception:
                    pass

    async def connect_lavalink(self):
        global music_cog_session
        await self.bot.wait_until_ready()
        ws_url = f"{'wss' if LAVALINK_SECURE else 'ws'}://{LAVALINK_HOST}:{LAVALINK_PORT}/v4/websocket"
        headers = {
            "Authorization": LAVALINK_PASSWORD,
            "User-Id": str(self.bot.user.id),
            "Client-Name": "HunterBot/1.0",
        }
        while not self.bot.is_closed():
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(ws_url, headers=headers) as ws:
                        log.info("Подключён к Lavalink")
                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                await self.handle_event(msg.json())
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
            except Exception as e:
                log.warning(f"Lavalink WS ошибка: {e}, переподключение через 5 сек")
                await asyncio.sleep(5)

    async def handle_event(self, data: dict):
        global music_cog_session
        op = data.get("op")
        if op == "ready":
            self.session_id = data.get("sessionId")
            music_cog_session = self.session_id
            log.info(f"Lavalink session: {self.session_id}")
            for p in players.values():
                p.session_id = self.session_id

        elif op == "playerUpdate":
            guild_id = int(data.get("guildId", 0))
            player = players.get(guild_id)
            if player:
                player.position = data.get("state", {}).get("position", 0)

        elif op == "event":
            event_type = data.get("type")
            guild_id = int(data.get("guildId", 0))
            player = players.get(guild_id)
            if not player:
                return
            if event_type == "TrackEndEvent" and data.get("reason") == "finished":
                if player.queue:
                    player.current = player.queue.pop(0)
                    player.position = 0
                    await lavalink_request(
                        "PATCH", f"/v4/sessions/{self.session_id}/players/{guild_id}",
                        json={"track": {"encoded": player.current.get("encoded")}}
                    )
                else:
                    player.current = None
                    player.position = 0
                if player.message:
                    try:
                        await player.message.edit(embed=build_embed(player), view=MusicView(guild_id) if player.current else None)
                    except Exception:
                        pass

    @commands.Cog.listener()
    async def on_socket_raw_receive(self, msg: str):
        try:
            data = json.loads(msg)
        except Exception:
            return
        t = data.get("t")
        d = data.get("d") or {}
        if not self.session_id:
            return
        guild_id = int(d.get("guild_id", 0) or 0)
        if not guild_id:
            return
        player = players.get(guild_id)
        if not player:
            return
        if t == "VOICE_STATE_UPDATE" and str(d.get("user_id")) == str(self.bot.user.id):
            player._voice_session_id = d.get("session_id")
            await self._send_voice_update(guild_id, player)
        elif t == "VOICE_SERVER_UPDATE":
            player._voice_token = d.get("token")
            player._voice_endpoint = d.get("endpoint", "")
            await self._send_voice_update(guild_id, player)

    async def _send_voice_update(self, guild_id: int, player):
        token = getattr(player, "_voice_token", None)
        endpoint = getattr(player, "_voice_endpoint", None)
        session_id = getattr(player, "_voice_session_id", None)
        if not all([token, endpoint, session_id]):
            return
        await lavalink_request(
            "PATCH", f"/v4/sessions/{self.session_id}/players/{guild_id}",
            json={"voice": {"token": token, "endpoint": endpoint, "sessionId": session_id}}
        )
        pending = getattr(player, "_pending_track", None)
        if pending:
            player._pending_track = None
            await lavalink_request(
                "PATCH", f"/v4/sessions/{self.session_id}/players/{guild_id}",
                json={"track": {"encoded": pending.get("encoded")}}
            )
            log.info(f"Трек запущен в Lavalink для guild {guild_id}")

    @commands.slash_command(
        description="Установить канал для музыкального плеера",
        default_member_permissions=disnake.Permissions(administrator=True)
    )
    async def set_music_channel(self, inter: disnake.ApplicationCommandInteraction, channel: disnake.TextChannel):
        db_set_music_channel(inter.guild.id, channel.id)
        await inter.response.send_message(f"✅ Музыкальный канал: {channel.mention}", ephemeral=True)

    @commands.slash_command(description="Воспроизвести трек в голосовом канале")
    async def music(self, inter: disnake.ApplicationCommandInteraction, query: str):
        music_ch_id = get_music_channel(inter.guild.id)
        if music_ch_id and inter.channel.id != music_ch_id:
            ch = inter.guild.get_channel(music_ch_id)
            return await inter.response.send_message(f"❌ Используй команду в {ch.mention}.", ephemeral=True)
        if not inter.author.voice:
            return await inter.response.send_message("❌ Зайди в голосовой канал.", ephemeral=True)
        if not self.session_id:
            return await inter.response.send_message("❌ Lavalink не подключён.", ephemeral=True)

        await inter.response.defer()
        track = await search_track(query)
        if not track:
            return await inter.edit_original_response(content="❌ Трек не найден.")

        player = get_player(inter.guild.id)
        player.session_id = self.session_id

        await self.bot.ws.voice_state(inter.guild.id, inter.author.voice.channel.id, self_mute=False, self_deaf=False)

        if not player.current:
            player.current = track
            player.position = 0
            player._pending_track = track
        else:
            player.queue.append(track)

        log.info(f"[{inter.guild.name}] Трек: {track.get('info', {}).get('title')} ({inter.author})")

        if player.message:
            try:
                await player.message.delete()
            except Exception:
                pass

        player.message = await inter.edit_original_response(embed=build_embed(player), view=MusicView(inter.guild.id))


def setup(bot: commands.InteractionBot):
    bot.add_cog(Music(bot))
