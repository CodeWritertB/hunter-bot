import disnake
import logging
import time
from datetime import datetime, timezone
from disnake.ext import commands
from db.database import upsert_member, bulk_upsert_members, increment_messages, add_voice_minutes, increment_server_messages, add_server_voice_minutes, update_peak_online

log = logging.getLogger("cogs.tracker")

# Словарь активных голосовых сессий: guild_id -> {user_id -> время входа}
voice_sessions: dict[int, dict[int, float]] = {}


def now_iso() -> str:
    """Возвращает текущее время в формате ISO 8601 (UTC)."""
    return datetime.now(timezone.utc).isoformat()


class Tracker(commands.Cog):
    def __init__(self, bot: commands.InteractionBot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        """При старте синхронизируем всех участников батчем и обновляем пик онлайна."""
        now = now_iso()
        for guild in self.bot.guilds:
            online = sum(1 for m in guild.members if m.status != disnake.Status.offline)
            update_peak_online(guild.id, online, now)
            rows = [
                (guild.id, m.id, str(m), m.display_name,
                 m.joined_at.isoformat() if m.joined_at else None, now)
                for m in guild.members if not m.bot
            ]
            if rows:
                bulk_upsert_members(rows)
        log.info("Синхронизация участников завершена")

    @commands.Cog.listener()
    async def on_member_join(self, member: disnake.Member):
        """Добавляем нового участника в БД."""
        if member.bot:
            return
        upsert_member(
            member.guild.id, member.id,
            str(member), member.display_name,
            member.joined_at.isoformat() if member.joined_at else None,
            now_iso()
        )

    @commands.Cog.listener()
    async def on_member_update(self, before: disnake.Member, after: disnake.Member):
        """Обновляем ник/имя участника при изменении."""
        if after.bot:
            return
        if before.display_name != after.display_name or str(before) != str(after):
            upsert_member(
                after.guild.id, after.id,
                str(after), after.display_name,
                after.joined_at.isoformat() if after.joined_at else None,
                now_iso()
            )

    @commands.Cog.listener()
    async def on_presence_update(self, before: disnake.Member, after: disnake.Member):
        """Обновляем пик онлайна при изменении статуса участника."""
        if after.bot:
            return
        online = sum(1 for m in after.guild.members if m.status != disnake.Status.offline)
        update_peak_online(after.guild.id, online, now_iso())

    @commands.Cog.listener()
    async def on_message(self, message: disnake.Message):
        """Считаем сообщения участника и сервера."""
        if not message.guild or message.author.bot:
            return
        increment_messages(message.guild.id, message.author.id, now_iso())
        increment_server_messages(message.guild.id)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: disnake.Member, before: disnake.VoiceState, after: disnake.VoiceState):
        """Отслеживаем время в войсе: фиксируем вход и считаем минуты при выходе."""
        if member.bot:
            return
        guild_id = member.guild.id
        if guild_id not in voice_sessions:
            voice_sessions[guild_id] = {}

        # Зашёл в войс — запоминаем время
        if not before.channel and after.channel:
            voice_sessions[guild_id][member.id] = time.time()

        # Вышел из войса — считаем минуты и сохраняем
        elif before.channel and not after.channel:
            join_time = voice_sessions.get(guild_id, {}).pop(member.id, None)
            if join_time:
                minutes = int((time.time() - join_time) / 60)
                if minutes > 0:
                    add_voice_minutes(guild_id, member.id, minutes, now_iso())
                    add_server_voice_minutes(guild_id, minutes)


def setup(bot: commands.InteractionBot):
    bot.add_cog(Tracker(bot))
