import asyncio
import logging
import aiohttp
import disnake
from disnake.ext import commands
from db.database import get_music_channel, set_music_channel as db_set_music_channel

log = logging.getLogger("cogs.music")

# Публичная Lavalink нода (v4)
LAVALINK_HOST = "lava-v4.ajieblogs.eu.org"
LAVALINK_PORT = 80
LAVALINK_PASSWORD = "https://dsc.gg/ajidevserver"
LAVALINK_SECURE = False

LAVALINK_BASE = f"{'https' if LAVALINK_SECURE else 'http'}://{LAVALINK_HOST}:{LAVALINK_PORT}"
LAVALINK_HEADERS = {
    "Authorization": LAVALINK_PASSWORD,
    "Content-Type": "application/json",
}


class MusicPlayer:
    """Состояние плеера для одного сервера."""
    def __init__(self):
        self.queue: list[dict] = []
        self.current: dict | None = None
        self.message: disnake.Message | None = None
        self.session_id: str | None = None
        self.voice_channel: disnake.VoiceChannel | None = None


# guild_id -> MusicPlayer
players: dict[int, MusicPlayer] = {}


def get_player(guild_id: int) -> MusicPlayer:
    if guild_id not in players:
        players[guild_id] = MusicPlayer()
    return players[guild_id]


async def search_track(query: str) -> dict | None:
    """Ищет трек через Lavalink REST API."""
    if not query.startswith("http"):
        query = f"ytsearch:{query}"
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{LAVALINK_BASE}/v4/loadtracks",
            headers=LAVALINK_HEADERS,
            params={"identifier": query}
        ) as r:
            if r.status != 200:
                return None
            data = await r.json()
            load_type = data.get("loadType")
            if load_type == "track":
                return data["data"]
            elif load_type == "search":
                tracks = data.get("data", [])
                return tracks[0] if tracks else None
            return None


def build_embed(player: MusicPlayer) -> disnake.Embed:
    """Строит embed плеера."""
    embed = disnake.Embed(title="🎵 Музыкальный плеер", color=disnake.Color.blurple())
    if player.current:
        info = player.current.get("info", {})
        title = info.get("title", "Неизвестно")
        uri = info.get("uri", "")
        embed.add_field(name="Сейчас играет", value=f"[{title}]({uri})" if uri else title, inline=False)
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
    """Панель управления плеером."""
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    @disnake.ui.button(emoji="⏸", style=disnake.ButtonStyle.secondary)
    async def pause_btn(self, button, inter: disnake.MessageInteraction):
        player = get_player(self.guild_id)
        # Пауза через Lavalink REST
        async with aiohttp.ClientSession() as session:
            await session.patch(
                f"{LAVALINK_BASE}/v4/sessions/{player.session_id}/players/{self.guild_id}",
                headers=LAVALINK_HEADERS,
                json={"paused": True}
            )
        await inter.response.edit_message(embed=build_embed(player))

    @disnake.ui.button(emoji="▶️", style=disnake.ButtonStyle.secondary)
    async def resume_btn(self, button, inter: disnake.MessageInteraction):
        player = get_player(self.guild_id)
        async with aiohttp.ClientSession() as session:
            await session.patch(
                f"{LAVALINK_BASE}/v4/sessions/{player.session_id}/players/{self.guild_id}",
                headers=LAVALINK_HEADERS,
                json={"paused": False}
            )
        await inter.response.edit_message(embed=build_embed(player))

    @disnake.ui.button(emoji="⏭", style=disnake.ButtonStyle.secondary)
    async def skip_btn(self, button, inter: disnake.MessageInteraction):
        player = get_player(self.guild_id)
        if player.queue:
            player.current = player.queue.pop(0)
            track_encoded = player.current.get("encoded")
            async with aiohttp.ClientSession() as session:
                await session.patch(
                    f"{LAVALINK_BASE}/v4/sessions/{player.session_id}/players/{self.guild_id}",
                    headers=LAVALINK_HEADERS,
                    json={"track": {"encoded": track_encoded}}
                )
        else:
            player.current = None
        await inter.response.edit_message(embed=build_embed(player))

    @disnake.ui.button(emoji="⏹", style=disnake.ButtonStyle.danger)
    async def stop_btn(self, button, inter: disnake.MessageInteraction):
        player = get_player(self.guild_id)
        player.queue.clear()
        player.current = None
        async with aiohttp.ClientSession() as session:
            await session.delete(
                f"{LAVALINK_BASE}/v4/sessions/{player.session_id}/players/{self.guild_id}",
                headers=LAVALINK_HEADERS,
            )
        # Отключаемся от войса
        vc = inter.guild.voice_client
        if vc:
            await vc.disconnect()
        await inter.response.edit_message(embed=build_embed(player), view=None)


class Music(commands.Cog):
    def __init__(self, bot: commands.InteractionBot):
        self.bot = bot
        self.session_id: str | None = None
        self.ws_task = bot.loop.create_task(self.connect_lavalink())

    def cog_unload(self):
        self.ws_task.cancel()

    async def connect_lavalink(self):
        """Подключается к Lavalink через WebSocket и слушает события."""
        await self.bot.wait_until_ready()
        bot_id = self.bot.user.id
        ws_url = f"{'wss' if LAVALINK_SECURE else 'ws'}://{LAVALINK_HOST}:{LAVALINK_PORT}/v4/websocket"
        headers = {
            "Authorization": LAVALINK_PASSWORD,
            "User-Id": str(bot_id),
            "Client-Name": "HunterBot/1.0",
        }
        while not self.bot.is_closed():
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(ws_url, headers=headers) as ws:
                        log.info("Подключён к Lavalink")
                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                data = msg.json()
                                await self.handle_event(data)
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
            except Exception as e:
                log.warning(f"Lavalink WS ошибка: {e}, переподключение через 5 сек")
                await asyncio.sleep(5)

    async def handle_event(self, data: dict):
        """Обрабатывает события от Lavalink."""
        op = data.get("op")
        if op == "ready":
            self.session_id = data.get("sessionId")
            log.info(f"Lavalink session: {self.session_id}")
            # Обновляем session_id во всех плеерах
            for p in players.values():
                p.session_id = self.session_id

        elif op == "event":
            event_type = data.get("type")
            guild_id = int(data.get("guildId", 0))
            player = players.get(guild_id)
            if not player:
                return

            if event_type == "TrackEndEvent":
                reason = data.get("reason")
                if reason == "finished" and player.queue:
                    # Играем следующий трек
                    player.current = player.queue.pop(0)
                    track_encoded = player.current.get("encoded")
                    async with aiohttp.ClientSession() as s:
                        await s.patch(
                            f"{LAVALINK_BASE}/v4/sessions/{self.session_id}/players/{guild_id}",
                            headers=LAVALINK_HEADERS,
                            json={"track": {"encoded": track_encoded}}
                        )
                else:
                    player.current = None

                # Обновляем сообщение плеера
                if player.message:
                    try:
                        await player.message.edit(embed=build_embed(player))
                    except Exception:
                        pass

    @commands.slash_command(
        description="Установить канал для музыкального плеера",
        default_member_permissions=disnake.Permissions(administrator=True)
    )
    async def set_music_channel(self, inter: disnake.ApplicationCommandInteraction, channel: disnake.TextChannel):
        db_set_music_channel(inter.guild.id, channel.id)
        log.info(f"[{inter.guild.name}] Музыкальный канал: #{channel.name} ({inter.author})")
        await inter.response.send_message(f"✅ Музыкальный канал: {channel.mention}", ephemeral=True)

    @commands.slash_command(description="Воспроизвести трек в голосовом канале")
    async def music(self, inter: disnake.ApplicationCommandInteraction, query: str):
        # Проверяем канал
        music_ch_id = get_music_channel(inter.guild.id)
        if music_ch_id and inter.channel.id != music_ch_id:
            ch = inter.guild.get_channel(music_ch_id)
            return await inter.response.send_message(
                f"❌ Используй команду в {ch.mention}.", ephemeral=True
            )
        if not inter.author.voice:
            return await inter.response.send_message("❌ Зайди в голосовой канал.", ephemeral=True)
        if not self.session_id:
            return await inter.response.send_message("❌ Lavalink не подключён, попробуй позже.", ephemeral=True)

        await inter.response.defer()

        # Ищем трек
        track = await search_track(query)
        if not track:
            return await inter.edit_original_response(content="❌ Трек не найден.")

        player = get_player(inter.guild.id)
        player.session_id = self.session_id
        player.voice_channel = inter.author.voice.channel

        # Подключаемся к войсу если ещё не подключены
        vc = inter.guild.voice_client
        if not vc:
            vc = await inter.author.voice.channel.connect()

        # Отправляем voice update в Lavalink
        async with aiohttp.ClientSession() as session:
            await session.patch(
                f"{LAVALINK_BASE}/v4/sessions/{self.session_id}/players/{inter.guild.id}",
                headers=LAVALINK_HEADERS,
                json={
                    "voice": {
                        "token": vc.token if hasattr(vc, 'token') else "",
                        "endpoint": vc.endpoint if hasattr(vc, 'endpoint') else "",
                        "sessionId": vc.session_id if hasattr(vc, 'session_id') else "",
                    }
                }
            )

        if not player.current:
            # Начинаем воспроизведение
            player.current = track
            async with aiohttp.ClientSession() as session:
                await session.patch(
                    f"{LAVALINK_BASE}/v4/sessions/{self.session_id}/players/{inter.guild.id}",
                    headers=LAVALINK_HEADERS,
                    json={"track": {"encoded": track.get("encoded")}}
                )
        else:
            player.queue.append(track)

        title = track.get("info", {}).get("title", "Неизвестно")
        log.info(f"[{inter.guild.name}] Трек: {title} ({inter.author})")

        # Удаляем старое сообщение плеера
        if player.message:
            try:
                await player.message.delete()
            except Exception:
                pass

        player.message = await inter.edit_original_response(
            embed=build_embed(player),
            view=MusicView(inter.guild.id)
        )


def setup(bot: commands.InteractionBot):
    bot.add_cog(Music(bot))
