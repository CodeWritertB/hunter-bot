import asyncio
import logging
import os
import re

import disnake
from disnake.ext import commands
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from db.database import get_music_channel, set_music_channel as db_set_music_channel

log = logging.getLogger("cogs.music")
FFMPEG_EXECUTABLE = os.getenv("FFMPEG_PATH", "ffmpeg").strip() or "ffmpeg"
MAX_PLAYLIST_TRACKS = 50
IDLE_TIMEOUT = 300


def is_spotify_url(query: str) -> bool:
    return bool(re.search(r"(?:open\.spotify\.com|spotify:)", query, flags=re.IGNORECASE))


def format_search_query(info: dict) -> str:
    title = info.get("title") or ""
    artist = info.get("artist") or info.get("uploader") or ""
    return f"{title} {artist}".strip()


def _extract_info(query: str, *, flat: bool = False) -> dict | None:
    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": not flat,
        "playlistend": MAX_PLAYLIST_TRACKS,
        "extract_flat": "in_playlist" if flat else False,
    }
    if not flat:
        options["format"] = "bestaudio/best"
    with YoutubeDL(options) as ydl:
        return ydl.extract_info(query, download=False)


def _track_from_info(info: dict) -> dict | None:
    if not info:
        return None
    track_id = info.get("id")
    uri = (
        info.get("webpage_url")
        or info.get("original_url")
        or info.get("url")
    )
    if uri and track_id and not uri.startswith(("http://", "https://")):
        uri = f"https://www.youtube.com/watch?v={track_id}"
    if not uri:
        return None

    duration = info.get("duration") or 0
    return {
        "info": {
            "title": info.get("title") or "Неизвестный трек",
            "uri": uri,
            "length": int(duration * 1000),
        }
    }


async def extract_spotify_items(query: str) -> list[str] | None:
    try:
        info = await asyncio.to_thread(_extract_info, query, flat=True)
    except DownloadError:
        log.warning("Не удалось получить метаданные Spotify-ссылки")
        return None

    if not info:
        return None
    if info.get("_type") in {"playlist", "multi_video"} or info.get("entries"):
        return [
            formatted
            for entry in (info.get("entries") or [])[:MAX_PLAYLIST_TRACKS]
            if entry and (formatted := format_search_query(entry))
        ]
    formatted = format_search_query(info)
    return [formatted] if formatted else None


async def search_track(query: str) -> dict | list[dict] | None:
    if is_spotify_url(query):
        items = await extract_spotify_items(query)
        if not items:
            return None
        tracks: list[dict] = []
        for item in items[:MAX_PLAYLIST_TRACKS]:
            result = await search_track(item)
            if isinstance(result, list):
                tracks.extend(result)
            elif result:
                tracks.append(result)
            if len(tracks) >= MAX_PLAYLIST_TRACKS:
                break
        return tracks[:MAX_PLAYLIST_TRACKS] or None

    identifier = query if query.startswith(("http://", "https://")) else f"ytsearch1:{query}"
    try:
        info = await asyncio.to_thread(_extract_info, identifier, flat=True)
    except DownloadError:
        log.warning("yt-dlp не смог найти трек по запросу")
        return None

    if not info:
        return None
    if info.get("_type") in {"playlist", "multi_video"} or info.get("entries"):
        tracks = [
            track
            for entry in (info.get("entries") or [])[:MAX_PLAYLIST_TRACKS]
            if entry and (track := _track_from_info(entry))
        ]
        return tracks or None
    return _track_from_info(info)


def _resolve_audio_url(uri: str) -> str:
    info = _extract_info(uri)
    if info and info.get("entries"):
        info = next((entry for entry in info["entries"] if entry), None)
    if not info or not info.get("url"):
        raise DownloadError("yt-dlp не вернул прямой URL аудиопотока")
    return info["url"]


def fmt_time(ms: int) -> str:
    seconds = max(ms, 0) // 1000
    return f"{seconds // 60}:{seconds % 60:02d}"


def progress_bar(position: int, duration: int, length: int = 12) -> str:
    if duration <= 0:
        return "─" * length
    filled = max(0, min(length, int(position / duration * length)))
    return "▬" * filled + ("🔘" if filled < length else "") + "─" * max(0, length - filled - 1)


class MusicPlayer:
    def __init__(self):
        self.queue: list[dict] = []
        self.current: dict | None = None
        self.message: disnake.Message | None = None
        self.paused = False
        self.position = 0
        self.voice_channel_id: int | None = None
        self.started_at = 0.0
        self.idle_since: float | None = None
        self.generation = 0
        self.lock = asyncio.Lock()


players: dict[int, MusicPlayer] = {}


def get_player(guild_id: int) -> MusicPlayer:
    if guild_id not in players:
        players[guild_id] = MusicPlayer()
    return players[guild_id]


def build_embed(player: MusicPlayer) -> disnake.Embed:
    embed = disnake.Embed(title="🎵 Музыкальный плеер", color=disnake.Color.blurple())
    if player.current:
        info = player.current.get("info", {})
        title = info.get("title", "Неизвестно")
        uri = info.get("uri", "")
        duration = info.get("length", 0)
        status = "⏸ Пауза" if player.paused else "▶️ Играет"
        progress = f"`{fmt_time(player.position)} {progress_bar(player.position, duration)} {fmt_time(duration)}`"
        embed.add_field(
            name=f"{status} — [{title}]({uri})" if uri else f"{status} — {title}",
            value=progress,
            inline=False,
        )
    else:
        embed.add_field(name="Сейчас играет", value="Ничего", inline=False)

    if player.queue:
        lines = [f"{i + 1}. {track.get('info', {}).get('title', '?')}" for i, track in enumerate(player.queue[:5])]
        if len(player.queue) > 5:
            lines.append(f"...и ещё {len(player.queue) - 5}")
        embed.add_field(name="Очередь", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="Очередь", value="Пусто", inline=False)
    return embed


class MusicView(disnake.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=1800)
        self.guild_id = guild_id
        self._update_pause_button()

    def _update_pause_button(self):
        player = players.get(self.guild_id)
        paused = player.paused if player else False
        for item in self.children:
            if getattr(item, "custom_id", None) == "pause_toggle":
                item.emoji = "▶️" if paused else "⏸"
                item.style = disnake.ButtonStyle.success if paused else disnake.ButtonStyle.secondary
                break

    @disnake.ui.button(emoji="⏸", style=disnake.ButtonStyle.secondary, custom_id="pause_toggle")
    async def pause_toggle(self, _button: disnake.ui.Button, inter: disnake.MessageInteraction):
        player = get_player(self.guild_id)
        voice = inter.guild.voice_client if inter.guild else None
        if not player.current or not voice or not voice.is_connected():
            return await inter.response.send_message("Сейчас ничего не воспроизводится.", ephemeral=True)

        if voice.is_paused():
            voice.resume()
            player.paused = False
            player.started_at = asyncio.get_running_loop().time() - player.position / 1000
        elif voice.is_playing():
            player.position = Music.current_position(player)
            voice.pause()
            player.paused = True
        self._update_pause_button()
        await inter.response.edit_message(embed=build_embed(player), view=self)

    @disnake.ui.button(emoji="⏭", style=disnake.ButtonStyle.secondary, custom_id="music_skip")
    async def skip_btn(self, _button: disnake.ui.Button, inter: disnake.MessageInteraction):
        voice = inter.guild.voice_client if inter.guild else None
        if not voice or not voice.is_playing() and not voice.is_paused():
            return await inter.response.send_message("Сейчас нечего пропускать.", ephemeral=True)
        voice.stop()
        await inter.response.defer()

    @disnake.ui.button(emoji="⏹", style=disnake.ButtonStyle.danger, custom_id="music_stop")
    async def stop_btn(self, _button: disnake.ui.Button, inter: disnake.MessageInteraction):
        player = get_player(self.guild_id)
        player.generation += 1
        player.queue.clear()
        player.current = None
        player.position = 0
        player.paused = False
        player.idle_since = asyncio.get_running_loop().time()
        voice = inter.guild.voice_client if inter.guild else None
        if voice:
            voice.stop()
            await voice.disconnect()
        player.voice_channel_id = None
        await inter.response.edit_message(embed=build_embed(player), view=None)


class Music(commands.Cog):
    def __init__(self, bot: commands.InteractionBot):
        self.bot = bot
        self.update_task = bot.loop.create_task(self.progress_update_loop())

    def cog_unload(self):
        self.update_task.cancel()

    @staticmethod
    def current_position(player: MusicPlayer) -> int:
        if player.paused or not player.current:
            return player.position
        elapsed = asyncio.get_running_loop().time() - player.started_at
        position = max(0, int(elapsed * 1000))
        duration = player.current["info"].get("length", 0)
        return min(duration, position) if duration else position

    async def update_player_message(self, player: MusicPlayer, *, active: bool):
        if not player.message:
            return
        try:
            await player.message.edit(
                embed=build_embed(player),
                view=MusicView(player.message.guild.id) if active and player.message.guild else None,
            )
        except (disnake.NotFound, disnake.Forbidden, disnake.HTTPException):
            log.exception("Не удалось обновить сообщение музыкального плеера")

    def schedule_track_end(self, guild_id: int, generation: int, error: Exception | None):
        if error:
            log.error("[guild=%s] Ошибка воспроизведения: %s", guild_id, error)
        self.bot.loop.call_soon_threadsafe(
            lambda: self.bot.loop.create_task(self.on_track_end(guild_id, generation))
        )

    async def on_track_end(self, guild_id: int, generation: int):
        player = players.get(guild_id)
        if not player or player.generation != generation:
            return
        player.current = None
        player.position = 0
        player.paused = False
        await self.start_next_track(guild_id)

    async def start_next_track(self, guild_id: int) -> bool:
        player = get_player(guild_id)
        async with player.lock:
            guild = self.bot.get_guild(guild_id)
            voice = guild.voice_client if guild else None
            if not guild or not voice or not voice.is_connected():
                player.current = None
                player.queue.clear()
                await self.update_player_message(player, active=False)
                return False

            while player.queue:
                track = player.queue.pop(0)
                player.current = track
                player.position = 0
                player.paused = False
                try:
                    uri = track["info"]["uri"]
                    audio_url = await asyncio.to_thread(_resolve_audio_url, uri)
                    source = disnake.FFmpegOpusAudio(
                        audio_url,
                        executable=FFMPEG_EXECUTABLE,
                        bitrate=128,
                        before_options="-nostdin -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
                        options="-vn",
                    )
                    player.generation += 1
                    generation = player.generation
                    player.started_at = asyncio.get_running_loop().time()
                    player.idle_since = None
                    voice.play(
                        source,
                        after=lambda error: self.schedule_track_end(guild_id, generation, error),
                    )
                    log.info("[guild=%s] Запущен трек: %s", guild_id, track["info"]["title"])
                    await self.update_player_message(player, active=True)
                    return True
                except (DownloadError, OSError, disnake.DiscordException):
                    log.exception(
                        "[guild=%s] Не удалось запустить трек: %s",
                        guild_id,
                        track.get("info", {}).get("title", "unknown"),
                    )
            player.current = None
            player.position = 0
            player.paused = False
            player.idle_since = asyncio.get_running_loop().time()
            await self.update_player_message(player, active=False)
            return False

    async def progress_update_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            await asyncio.sleep(10)
            now = asyncio.get_running_loop().time()
            for guild_id, player in list(players.items()):
                guild = self.bot.get_guild(guild_id)
                voice = guild.voice_client if guild else None
                if not player.current:
                    if player.idle_since is None:
                        player.idle_since = now
                    elif now - player.idle_since >= IDLE_TIMEOUT:
                        if voice and voice.is_connected():
                            await voice.disconnect()
                        player.voice_channel_id = None
                        player.idle_since = None
                    continue
                if voice and voice.is_playing():
                    player.position = self.current_position(player)
                    await self.update_player_message(player, active=True)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: disnake.Member,
        before: disnake.VoiceState,
        _after: disnake.VoiceState,
    ):
        if member.bot or not before.channel:
            return
        player = players.get(member.guild.id)
        if not player or not player.current or before.channel.id != player.voice_channel_id:
            return
        remaining_humans = [voice_member for voice_member in before.channel.members if voice_member.id != member.id and not voice_member.bot]
        if not remaining_humans:
            player.generation += 1
            player.queue.clear()
            player.current = None
            player.position = 0
            voice = member.guild.voice_client
            if voice:
                voice.stop()
                await voice.disconnect()
            player.voice_channel_id = None
            player.idle_since = asyncio.get_running_loop().time()
            await self.update_player_message(player, active=False)
            log.info("[%s] Канал пуст — музыка остановлена", member.guild.name)

    @commands.slash_command(
        description="Установить канал для музыкального плеера",
        default_member_permissions=disnake.Permissions(administrator=True),
    )
    async def set_music_channel(self, inter: disnake.ApplicationCommandInteraction, channel: disnake.TextChannel):
        db_set_music_channel(inter.guild.id, channel.id)
        await inter.response.send_message(f"✅ Музыкальный канал: {channel.mention}", ephemeral=True)

    @commands.slash_command(description="Воспроизвести трек в голосовом канале")
    async def music(self, inter: disnake.ApplicationCommandInteraction, query: str):
        music_channel_id = get_music_channel(inter.guild.id)
        if music_channel_id and inter.channel.id != music_channel_id:
            channel = inter.guild.get_channel(music_channel_id)
            return await inter.response.send_message(
                f"❌ Используй команду в {channel.mention if channel else 'настроенном музыкальном канале'}.",
                ephemeral=True,
            )
        if not inter.author.voice or not inter.author.voice.channel:
            return await inter.response.send_message("❌ Зайди в голосовой канал.", ephemeral=True)

        await inter.response.defer()
        result = await search_track(query)
        tracks = result if isinstance(result, list) else [result] if result else []
        if not tracks:
            return await inter.edit_original_response(content="❌ Не удалось найти трек. Проверь ссылку или запрос.")

        player = get_player(inter.guild.id)
        voice = inter.guild.voice_client
        try:
            if voice and voice.is_connected():
                if voice.channel.id != inter.author.voice.channel.id:
                    if player.current:
                        return await inter.edit_original_response(
                            content=f"❌ Бот уже воспроизводит музыку в {voice.channel.mention}."
                        )
                    await voice.move_to(inter.author.voice.channel)
            else:
                voice = await inter.author.voice.channel.connect()
        except (disnake.Forbidden, disnake.ClientException, disnake.HTTPException):
            log.exception("[%s] Не удалось подключиться к голосовому каналу", inter.guild.name)
            return await inter.edit_original_response(
                content="❌ Не удалось подключиться к голосовому каналу. Проверь права бота и настройки голоса на хосте."
            )

        player.voice_channel_id = inter.author.voice.channel.id
        was_playing = player.current is not None
        player.queue.extend(tracks)
        player.idle_since = None
        if player.message:
            try:
                await player.message.delete()
            except (disnake.NotFound, disnake.Forbidden, disnake.HTTPException):
                log.warning("Не удалось удалить старое сообщение музыкального плеера")
        player.message = await inter.edit_original_response(
            content=None,
            embed=build_embed(player),
            view=MusicView(inter.guild.id),
        )

        if not was_playing:
            if not await self.start_next_track(inter.guild.id):
                return await inter.edit_original_response(
                    content=(
                        "❌ Не удалось запустить аудио. Проверьте, что FFmpeg установлен на хосте "
                        f"(исполняемый файл: `{FFMPEG_EXECUTABLE}`) и ссылка доступна."
                    ),
                    embed=None,
                    view=None,
                )
        added_count = len(tracks)
        log.info(
            "[%s] Музыка запрошена пользователем %s; добавлено в очередь: %s",
            inter.guild.name,
            inter.author,
            added_count,
        )
        if was_playing:
            await inter.edit_original_response(
                content=f"✅ В очередь добавлено **{added_count}** трек(ов).",
                embed=build_embed(player),
                view=MusicView(inter.guild.id),
            )
        try:
            await player.message.pin()
            async for message in inter.channel.history(limit=5):
                if message.type == disnake.MessageType.pins_add:
                    await message.delete()
                    break
        except (disnake.Forbidden, disnake.HTTPException):
            log.debug("Не удалось закрепить сообщение плеера", exc_info=True)


def setup(bot: commands.InteractionBot):
    bot.add_cog(Music(bot))
