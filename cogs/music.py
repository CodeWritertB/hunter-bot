import asyncio
import logging
import os
import re
import tempfile
from urllib.parse import urlparse

import disnake
from disnake.ext import commands
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from db.database import get_music_channel, set_music_channel as db_set_music_channel

log = logging.getLogger("cogs.music")
FFMPEG_EXECUTABLE = os.getenv("FFMPEG_PATH", "ffmpeg").strip() or "ffmpeg"
MAX_PLAYLIST_TRACKS = 50
IDLE_TIMEOUT = 300


class YouTubeBotChallengeError(Exception):
    pass


class YtDlpLogger:
    def debug(self, message: str):
        if not message.startswith("[debug]"):
            log.debug("yt-dlp: %s", message)

    def warning(self, message: str):
        log.warning("yt-dlp: %s", message)

    def error(self, message: str):
        log.error("yt-dlp: %s", message)


def is_spotify_url(query: str) -> bool:
    return bool(re.search(r"(?:open\.spotify\.com|spotify:)", query, flags=re.IGNORECASE))


def format_search_query(info: dict) -> str:
    title = info.get("title") or ""
    artist = info.get("artist") or info.get("uploader") or ""
    return f"{title} {artist}".strip()


def _extract_info(query: str, *, flat: bool = False) -> dict | None:
    from cogs.music_repair import PACKAGES, extract, cookie_path
    if (PACKAGES / "yt_dlp" / "__main__.py").is_file():
        try:
            return extract(query, flat, MAX_PLAYLIST_TRACKS, cookie_path())
        except RuntimeError as error:
            if "sign in to confirm" in str(error).lower():
                raise YouTubeBotChallengeError("YouTube требует авторизацию") from error
            raise DownloadError("Не удалось загрузить данные YouTube") from error
    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": not flat,
        "playlistend": MAX_PLAYLIST_TRACKS,
        "extract_flat": "in_playlist" if flat else False,
        "logger": YtDlpLogger(),
    }
    if not flat:
        options["format"] = "bestaudio/best"
    options.update(socket_timeout=20, retries=2, js_runtimes={"node": {}})
    cookies = cookie_path()
    if cookies:
        options["cookiefile"] = cookies
    try:
        with YoutubeDL(options) as ydl:
            return ydl.extract_info(query, download=False)
    except DownloadError as error:
        message = str(error).casefold()
        if "sign in to confirm" in message and "not a bot" in message:
            raise YouTubeBotChallengeError(
                "YouTube is rejecting automated requests from this hosting provider."
            ) from error
        raise


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


async def search_track(query: str) -> dict | list[dict] | None:
    query = query.strip()
    if query.startswith(("http://", "https://")):
        host = (urlparse(query).hostname or "").lower()
        if host not in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}:
            raise ValueError("Поддерживаются только ссылки YouTube или поиск по названию.")
        identifier = query
    else:
        identifier = f"ytsearch1:{query}"
    info = await asyncio.to_thread(_extract_info, identifier, flat=True)
    if not info:
        return None
    entries = list(info.get("entries") or [info])[:MAX_PLAYLIST_TRACKS]
    return [track for entry in entries if entry and (track := _track_from_info(entry))] or None


def _resolve_audio_url(uri: str) -> dict:
    info = _extract_info(uri)
    if info and info.get("entries"):
        info = next((entry for entry in info["entries"] if entry), None)
    if not info or not info.get("url"):
        raise DownloadError("yt-dlp не вернул прямой URL аудиопотока")
    return {"url": info["url"], "headers": info.get("http_headers") or {}}


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
        self.expected_stop = False
        self.generation = 0
        self.playback_error: str | None = None
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
        embed.add_field(name="Не удалось воспроизвести" if player.playback_error else "Готов к воспроизведению", value="Зайди в голосовой канал и нажми «Добавить трек». Введи название песни или ссылку YouTube.", inline=False)

    if player.queue:
        lines = [f"{i + 1}. {track.get('info', {}).get('title', '?')}" for i, track in enumerate(player.queue[:5])]
        if len(player.queue) > 5:
            lines.append(f"...и ещё {len(player.queue) - 5}")
        embed.add_field(name="Очередь", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="Очередь", value="Пусто", inline=False)
    if player.playback_error:
        embed.add_field(name="Как восстановить доступ", value="Обнови компоненты кнопкой восстановления. Если YouTube требует вход: открой YouTube в браузере. Экспортируй cookies своей сессии YouTube в Netscape-формате и вставь через «Настроить YouTube» (до 4000 символов). Это секрет сессии: используй отдельный аккаунт. Бот не получает cookies из браузера автоматически. Не передавай пароль и коды входа.", inline=False)
        embed.add_field(name="Ошибка воспроизведения", value=player.playback_error[:1024], inline=False)
    return embed


class YouTubeSettingsModal(disnake.ui.Modal):
    def __init__(self):
        super().__init__(title="Настройка YouTube", components=[
            disnake.ui.TextInput(label="Cookies YouTube в Netscape-формате", custom_id="cookies_text",
                                 style=disnake.TextInputStyle.paragraph, placeholder="# Netscape HTTP Cookie File\nВставь содержимое экспорта cookies, не пароль или код входа", required=True, max_length=4000)])

    async def callback(self, inter):
        if not inter.author.guild_permissions.administrator:
            return await inter.response.send_message("Нужны права администратора.", ephemeral=True)
        from cogs.music_repair import save_cookie_text
        raw = inter.text_values["cookies_text"].strip()
        try:
            result = save_cookie_text(raw)
        except (ValueError, OSError, UnicodeError) as error:
            log.warning("Не удалось подключить файл cookies: %s", type(error).__name__)
            return await inter.response.send_message("Cookies не сохранены. Нужен Netscape-экспорт с табуляцией и доменом youtube.com. Пароли, коды входа и JSON не подходят.", ephemeral=True)
        await inter.response.send_message(result, ephemeral=True)


class AddTrackModal(disnake.ui.Modal):
    def __init__(self):
        super().__init__(title="Добавить музыку", components=[
            disnake.ui.TextInput(label="Название трека или ссылка YouTube", custom_id="query",
                                 placeholder="Исполнитель — песня или https://youtu.be/...",
                                 min_length=1, max_length=500)])

    async def callback(self, inter):
        cog = inter.bot.get_cog("Music")
        if not cog:
            return await inter.response.send_message("Плеер временно недоступен.", ephemeral=True)
        query = inter.text_values["query"].strip()
        if not query:
            return await inter.response.send_message("Введи название или ссылку YouTube.", ephemeral=True)
        try:
            await cog.music(inter, query)
        except Exception:
            log.exception("Не удалось добавить трек через музыкальную панель")
            if inter.response.is_done():
                await inter.followup.send("Не удалось добавить трек. Попробуй позже.", ephemeral=True)
            else:
                await inter.response.send_message("Не удалось добавить трек. Попробуй позже.", ephemeral=True)


class MusicView(disnake.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self._update_pause_button()
        self.add_item(disnake.ui.Button(label="YouTube — войти в аккаунт", url="https://www.youtube.com/", emoji="🌐", row=2))

    async def interaction_check(self, inter):
        if getattr(inter.component, "custom_id", None) == "music_add":
            if not inter.guild or not inter.author.voice or not inter.author.voice.channel:
                await inter.response.send_message("Сначала зайди в голосовой канал.", ephemeral=True)
                return False
            return True
        if getattr(inter.component, "custom_id", None) in {"music_repair", "music_youtube_settings"}:
            if inter.author.guild_permissions.administrator:
                return True
            await inter.response.send_message("Восстановление запускает администратор сервера.", ephemeral=True)
            return False
        voice = inter.guild.voice_client if inter.guild else None
        if not voice or not inter.author.voice or inter.author.voice.channel != voice.channel:
            await inter.response.send_message("Зайди в голосовой канал бота, чтобы управлять музыкой.", ephemeral=True)
            return False
        return True

    @disnake.ui.button(label="Восстановить YouTube", emoji="🔧", style=disnake.ButtonStyle.secondary, custom_id="music_repair", row=1)
    async def repair_button(self, button, inter):
        await inter.response.defer(ephemeral=True)
        from cogs.music_repair import repair
        if any(p.current for p in players.values()):
            return await inter.edit_original_response(content="Сначала останови воспроизведение: обновление компонентов недоступно во время загрузки или проигрывания.")
        try:
            result = await repair()
            if result.startswith("Компоненты YouTube обновлены"):
                player = get_player(self.guild_id)
                player.playback_error = None
                cog = inter.bot.get_cog("Music")
                if cog:
                    await cog.update_player_message(player, active=False)
        except Exception:
            log.exception("Ошибка восстановления YouTube")
            result = "Не удалось обновить компоненты. Подробности в журнале бота."
        await inter.edit_original_response(content=result)

    @disnake.ui.button(label="Добавить трек", emoji="➕", style=disnake.ButtonStyle.primary, custom_id="music_add", row=0)
    async def add_track(self, button, inter):
        await inter.response.send_modal(AddTrackModal())

    @disnake.ui.button(label="Настроить YouTube", emoji="🔑", style=disnake.ButtonStyle.secondary, custom_id="music_youtube_settings", row=2)
    async def youtube_settings(self, button, inter):
        await inter.response.send_modal(YouTubeSettingsModal())

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
        get_player(self.guild_id).expected_stop = True
        voice.stop()
        await inter.response.defer()

    @disnake.ui.button(emoji="⏹", style=disnake.ButtonStyle.danger, custom_id="music_stop")
    async def stop_btn(self, _button: disnake.ui.Button, inter: disnake.MessageInteraction):
        await inter.response.defer()
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
        await inter.edit_original_response(embed=build_embed(player), view=self)


class Music(commands.Cog):
    def __init__(self, bot: commands.InteractionBot):
        self.bot = bot
        self.panel_lock = asyncio.Lock()
        self.update_task = bot.loop.create_task(self.progress_update_loop())

    def cog_unload(self):
        self.update_task.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            try:
                await self.ensure_panel(guild)
            except disnake.HTTPException:
                log.exception("Не удалось восстановить музыкальную панель")

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload):
        player = players.get(payload.guild_id)
        if player and player.message and player.message.id == payload.message_id:
            player.message = None
            guild = self.bot.get_guild(payload.guild_id)
            if guild:
                try:
                    await self.ensure_panel(guild)
                except disnake.HTTPException:
                    log.exception("Не удалось восстановить удалённый плеер")

    async def ensure_panel(self, guild):
        async with self.panel_lock:
            await self._ensure_panel(guild)

    async def _ensure_panel(self, guild):
        from db.database import _get_server, _upsert_server, ensure_column, conn
        ensure_column("servers", "music_message_id", "INTEGER")
        conn.commit()
        channel_id = get_music_channel(guild.id)
        channel = guild.get_channel(channel_id) if channel_id else None
        if not isinstance(channel, disnake.TextChannel):
            return
        player = get_player(guild.id)
        message_id = _get_server(guild.id, "music_message_id")
        if player.message and player.message.channel.id != channel.id:
            try:
                await player.message.delete()
            except disnake.HTTPException:
                pass
            player.message = None
        if message_id:
            try:
                player.message = await channel.fetch_message(message_id)
            except disnake.NotFound:
                player.message = None
        if not player.message:
            player.message = await channel.send(embed=build_embed(player), view=MusicView(guild.id))
            _upsert_server(guild.id, music_message_id=player.message.id)
        await self.update_player_message(player, active=True)

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
                view=MusicView(player.message.guild.id),
            )
        except (disnake.NotFound, disnake.Forbidden, disnake.HTTPException):
            log.exception("Не удалось обновить сообщение музыкального плеера")

    def schedule_track_end(self, guild_id, generation, error, stderr, process=None):
        try:
            stderr.seek(0, 2)
            size = stderr.tell()
            stderr.seek(max(0, size - 6000))
            detail = stderr.read().decode("utf-8", errors="replace")
        finally:
            stderr.close()
        # Do not log signed media URLs or authentication headers.
        safe = re.sub(r"https?://[^\s]+", "[URL]", detail)
        safe = "\n".join(line for line in safe.splitlines() if not re.search(r"cookie|authorization", line, re.I))
        if error:
            safe_error = re.sub(r"https?://[^\s]+", "[URL]", str(error))
            log.error("[guild=%s] Voice callback: %s: %s", guild_id, type(error).__name__, safe_error[:1000])
        exit_code = process.poll() if process is not None else None
        log.info("[guild=%s] Audio ended: ffmpeg_exit=%s; callback_error=%s; stderr_bytes=%s", guild_id, exit_code, type(error).__name__ if error else "none", size)
        if safe.strip():
            log.warning("[guild=%s] FFmpeg: %s", guild_id, safe[-3000:])
        self.bot.loop.call_soon_threadsafe(
            lambda: self.bot.loop.create_task(self.on_track_end(guild_id, generation, error, detail, exit_code))
        )

    async def on_track_end(self, guild_id, generation, error=None, detail="", exit_code=None):
        player = players.get(guild_id)
        if not player or player.generation != generation:
            return
        elapsed = asyncio.get_running_loop().time() - player.started_at
        duration = (player.current or {}).get("info", {}).get("length", 0) / 1000
        failed = not player.expected_stop and (error is not None or exit_code not in (None, 0) or (elapsed < 5 and (duration == 0 or duration > elapsed + 5)))
        player.expected_stop = False
        player.current = None
        player.position = 0
        player.paused = False
        if failed and not player.queue:
            reason = "YouTube отклонил сам аудиопоток (HTTP 403)." if "403" in detail else "Аудиопоток завершился сразу после запуска."
            if error:
                reason = f"Ошибка передачи звука в Discord ({type(error).__name__})."
            player.playback_error = reason + f" Код FFmpeg: {exit_code if exit_code is not None else 'не завершён'}. Подробности — в журнале бота: Audio ended / Voice callback / FFmpeg."
            log.error("[guild=%s] Раннее завершение аудио: %.2f сек", guild_id, elapsed)
            guild = self.bot.get_guild(guild_id)
            voice = guild.voice_client if guild else None
            if voice and voice.is_connected():
                await voice.disconnect(force=True)
            player.voice_channel_id = None
            player.idle_since = None
            await self.update_player_message(player, active=False)
            return
        await self.start_next_track(guild_id)

    async def start_next_track(self, guild_id: int) -> bool:
        player = get_player(guild_id)
        player.playback_error = None
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
                    generation_before_load = player.generation
                    audio = await asyncio.to_thread(_resolve_audio_url, uri)
                    if player.generation != generation_before_load:
                        return False
                    from cogs.music_repair import ffmpeg_path
                    headers = "".join(f"{name}: {value}\r\n" for name, value in audio["headers"].items()
                                      if name.lower() in {"user-agent", "referer", "origin"} and "\r" not in value and "\n" not in value)
                    import shlex
                    header_options = " -headers " + shlex.quote(headers) if headers else ""
                    stderr = tempfile.TemporaryFile()
                    source = disnake.FFmpegOpusAudio(
                        audio["url"],
                        executable=ffmpeg_path() or FFMPEG_EXECUTABLE,
                        bitrate=128,
                        before_options="-nostdin -loglevel warning -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5" + header_options,
                        stderr=stderr,
                        options="-vn",
                    )
                    player.expected_stop = False
                    player.generation += 1
                    generation = player.generation
                    player.started_at = asyncio.get_running_loop().time()
                    player.idle_since = None
                    player.playback_error = None
                    voice.play(
                        source,
                        after=lambda error, g=generation, diagnostic=stderr, proc=source._process: self.schedule_track_end(guild_id, g, error, diagnostic, proc),
                    )
                    log.info("[guild=%s] Запущен трек: %s", guild_id, track["info"]["title"])
                    await self.update_player_message(player, active=True)
                    return True
                except DownloadError as error:
                    player.playback_error = (
                        "Не удалось воспроизвести трек: источник недоступен или защищён DRM. "
                        "Попробуй другой результат или ссылку."
                    )
                    log.warning(
                        "[guild=%s] Пропущен недоступный трек %s: %s",
                        guild_id,
                        track.get("info", {}).get("title", "unknown"),
                        error,
                    )
                except YouTubeBotChallengeError:
                    player.playback_error = (
                        "YouTube требует подтверждение входа или отклонил запрос. Обновление компонентов не снимает эту блокировку. Нужна авторизованная сессия YouTube или решение ограничения на стороне хостинга."
                    )
                    log.warning(
                        "[guild=%s] YouTube отклонил запросы с IP хостинга",
                        guild_id,
                    )
                except (OSError, TimeoutError, disnake.DiscordException):
                    player.playback_error = "Не удалось запустить аудио. Проверь FFmpeg и права подключения бота."
                    log.exception(
                        "[guild=%s] Не удалось запустить трек: %s",
                        guild_id,
                        track.get("info", {}).get("title", "unknown"),
                    )
            player.current = None
            player.position = 0
            player.paused = False
            player.idle_since = asyncio.get_running_loop().time()
            if player.playback_error:
                player.generation += 1
                player.started_at = 0.0
                if voice.is_connected():
                    try:
                        await voice.disconnect(force=True)
                    except disnake.DiscordException:
                        log.exception("[guild=%s] Не удалось отключиться после ошибки аудио", guild_id)
                if not voice.is_connected():
                    player.voice_channel_id = None
                    player.idle_since = None
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
        if member.bot or not before.channel or before.channel == _after.channel:
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
        await self.ensure_panel(inter.guild)
        await inter.response.send_message(f"✅ Музыкальный канал: {channel.mention}", ephemeral=True)

    async def music(self, inter: disnake.ApplicationCommandInteraction, query: str):
        music_channel_id = get_music_channel(inter.guild.id)
        if not music_channel_id:
            return await inter.response.send_message("Администратор должен выбрать музыкальный канал через /admin.", ephemeral=True)
        if music_channel_id and inter.channel.id != music_channel_id:
            channel = inter.guild.get_channel(music_channel_id)
            return await inter.response.send_message(
                f"❌ Используй команду в {channel.mention if channel else 'настроенном музыкальном канале'}.",
                ephemeral=True,
            )
        if not inter.author.voice or not inter.author.voice.channel:
            return await inter.response.send_message("❌ Зайди в голосовой канал.", ephemeral=True)

        await inter.response.defer(ephemeral=True)
        try:
            result = await search_track(query)
        except (YouTubeBotChallengeError, DownloadError, ValueError) as error:
            log.warning("Ошибка поиска YouTube: %s", error)
            return await inter.edit_original_response(content="Не удалось загрузить YouTube. Проверь ссылку; при блокировке нужны актуальный yt-dlp, Node.js и cookies YouTube. Другие сервисы не используются.")
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
        except disnake.errors.ConnectionClosed as error:
            log.exception(
                "[%s] Голосовой WebSocket закрыт: code=%s",
                inter.guild.name,
                error.code,
            )
            if error.code == 4017:
                message = (
                    "❌ Discord требует DAVE/E2EE для голосового подключения. "
                    "Обнови зависимости бота командой `pip install -r requirements.txt` "
                    "и перезапусти его; нужна версия `disnake[voice]` с поддержкой DAVE."
                )
            else:
                message = f"❌ Discord закрыл голосовое подключение (код {error.code}). Попробуй позже."
            return await inter.edit_original_response(content=message)
        except (disnake.Forbidden, disnake.ClientException, disnake.HTTPException):
            log.exception("[%s] Не удалось подключиться к голосовому каналу", inter.guild.name)
            return await inter.edit_original_response(
                content="❌ Не удалось подключиться к голосовому каналу. Проверь права бота и настройки голоса на хосте."
            )

        player.voice_channel_id = inter.author.voice.channel.id
        was_playing = player.current is not None
        player.queue.extend(tracks)
        player.idle_since = None
        await self.ensure_panel(inter.guild)
        await self.update_player_message(player, active=True)

        if not was_playing:
            if not await self.start_next_track(inter.guild.id):
                return await inter.edit_original_response(
                    content=(
                        f"❌ {player.playback_error}"
                        if player.playback_error
                        else "❌ Не удалось запустить аудио. Проверьте, что FFmpeg установлен на хосте "
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
        await asyncio.sleep(0.5)
        if player.playback_error and not player.current:
            await inter.edit_original_response(content=f"❌ {player.playback_error}")
        else:
            await inter.edit_original_response(content=f"✅ Добавлено треков: {added_count}. Плеер находится в назначенном музыкальном канале.")


def setup(bot: commands.InteractionBot):
    bot.add_cog(Music(bot))
