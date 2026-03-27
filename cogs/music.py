import disnake
import logging
from disnake.ext import commands
import yt_dlp
import asyncio
from db.database import get_music_channel, set_music_channel as db_set_music_channel

log = logging.getLogger("cogs.music")

YDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
}

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}


def search_yt(query: str) -> dict | None:
    with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
        try:
            if not query.startswith("http"):
                query = f"ytsearch:{query}"
            info = ydl.extract_info(query, download=False)
            if "entries" in info:
                info = info["entries"][0]
            return {"title": info["title"], "url": info["url"], "webpage_url": info["webpage_url"]}
        except Exception:
            return None


class MusicPlayer:
    def __init__(self):
        self.queue: list[dict] = []
        self.current: dict | None = None
        self.message: disnake.Message | None = None

    def is_empty(self):
        return len(self.queue) == 0


players: dict[int, MusicPlayer] = {}


def get_player(guild_id: int) -> MusicPlayer:
    if guild_id not in players:
        players[guild_id] = MusicPlayer()
    return players[guild_id]


def build_embed(player: MusicPlayer, vc: disnake.VoiceClient) -> disnake.Embed:
    embed = disnake.Embed(title="🎵 Музыкальный плеер", color=disnake.Color.blurple())
    if player.current:
        embed.add_field(name="Сейчас играет", value=f"[{player.current['title']}]({player.current['webpage_url']})", inline=False)
    else:
        embed.add_field(name="Сейчас играет", value="Ничего", inline=False)

    if player.queue:
        queue_text = "\n".join(f"{i+1}. {t['title']}" for i, t in enumerate(player.queue[:5]))
        if len(player.queue) > 5:
            queue_text += f"\n...и ещё {len(player.queue) - 5}"
        embed.add_field(name="Очередь", value=queue_text, inline=False)
    else:
        embed.add_field(name="Очередь", value="Пусто", inline=False)

    status = "▶️ Играет" if vc and vc.is_playing() else ("⏸ Пауза" if vc and vc.is_paused() else "⏹ Остановлен")
    embed.set_footer(text=status)
    return embed


class MusicView(disnake.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    async def update(self, inter: disnake.MessageInteraction):
        player = get_player(self.guild_id)
        vc = inter.guild.voice_client
        await inter.response.edit_message(embed=build_embed(player, vc), view=self)

    @disnake.ui.button(emoji="⏸", style=disnake.ButtonStyle.secondary)
    async def pause(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        vc = inter.guild.voice_client
        if vc and vc.is_playing():
            vc.pause()
        elif vc and vc.is_paused():
            vc.resume()
        await self.update(inter)

    @disnake.ui.button(emoji="⏭", style=disnake.ButtonStyle.secondary)
    async def skip(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        vc = inter.guild.voice_client
        if vc and vc.is_playing():
            vc.stop()
        await self.update(inter)

    @disnake.ui.button(emoji="⏹", style=disnake.ButtonStyle.danger)
    async def stop(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        vc = inter.guild.voice_client
        player = get_player(self.guild_id)
        player.queue.clear()
        player.current = None
        if vc:
            vc.stop()
            await vc.disconnect()
        await self.update(inter)


class Music(commands.Cog):
    def __init__(self, bot: commands.InteractionBot):
        self.bot = bot

    def play_next(self, guild: disnake.Guild):
        player = get_player(guild.id)
        vc = guild.voice_client

        if not vc or not player.queue:
            player.current = None
            asyncio.run_coroutine_threadsafe(self.update_player_message(guild), self.bot.loop)
            return

        player.current = player.queue.pop(0)
        source = disnake.FFmpegPCMAudio(player.current["url"], **FFMPEG_OPTIONS)
        vc.play(source, after=lambda e: self.play_next(guild))
        asyncio.run_coroutine_threadsafe(self.update_player_message(guild), self.bot.loop)

    async def update_player_message(self, guild: disnake.Guild):
        player = get_player(guild.id)
        vc = guild.voice_client
        if player.message:
            try:
                await player.message.edit(embed=build_embed(player, vc), view=MusicView(guild.id))
            except Exception:
                pass

    @commands.slash_command(
        description="Установить канал для музыкального плеера",
        default_member_permissions=disnake.Permissions(administrator=True)
    )
    async def set_music_channel(self, inter: disnake.ApplicationCommandInteraction, channel: disnake.TextChannel):
        db_set_music_channel(inter.guild.id, channel.id)
        log.info(f"[{inter.guild.name}] Музыкальный канал установлен: #{channel.name} ({inter.author})")
        await inter.response.send_message(f"✅ Музыкальный канал установлен: {channel.mention}", ephemeral=True)

    @commands.slash_command(description="Открыть музыкальный плеер и добавить трек в очередь")
    async def music(self, inter: disnake.ApplicationCommandInteraction, query: str):
        music_channel_id = get_music_channel(inter.guild.id)
        if music_channel_id and inter.channel.id != music_channel_id:
            ch = inter.guild.get_channel(music_channel_id)
            return await inter.response.send_message(
                f"❌ Команду можно использовать только в {ch.mention}.", ephemeral=True
            )
        if not inter.author.voice:
            return await inter.response.send_message("❌ Зайди в голосовой канал.", ephemeral=True)

        await inter.response.defer()

        # Сначала подключаемся к голосу, потом ищем трек
        vc = inter.guild.voice_client
        if not vc:
            vc = await inter.author.voice.channel.connect()

        track = await asyncio.get_event_loop().run_in_executor(None, search_yt, query)
        if not track:
            return await inter.edit_original_response(content="❌ Трек не найден.")

        player = get_player(inter.guild.id)
        player.queue.append(track)
        log.info(f"[{inter.guild.name}] Трек добавлен в очередь: {track['title']} ({inter.author})")

        if not vc.is_playing() and not vc.is_paused():
            self.play_next(inter.guild)

        embed = build_embed(player, vc)
        view = MusicView(inter.guild.id)

        if player.message:
            try:
                await player.message.delete()
            except Exception:
                pass

        player.message = await inter.edit_original_response(embed=embed, view=view)


def setup(bot: commands.InteractionBot):
    bot.add_cog(Music(bot))
