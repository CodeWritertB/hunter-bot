import disnake
import logging
import asyncio
import re
from datetime import datetime, timezone, timedelta
from disnake.ext import commands
from db.database import update_streak, get_all_streaks, get_streak

log = logging.getLogger("cogs.streaks")

MSK = timezone(timedelta(hours=3))

# guild_id -> set of user_ids кто зашёл в войс сегодня
voice_today: dict[int, set[int]] = {}


def build_nick(base_nick: str, streak: int) -> str:
    base_nick = re.sub(r'\s*🔥\d+$', '', base_nick).strip()
    if streak >= 1:
        return f"{base_nick} 🔥{streak}"
    return base_nick


class Streaks(commands.Cog):
    def __init__(self, bot: commands.InteractionBot):
        self.bot = bot
        self.reset_task = bot.loop.create_task(self.daily_reset_loop())

    def cog_unload(self):
        self.reset_task.cancel()

    async def daily_reset_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            now = datetime.now(MSK)
            # Следующий 00:00 по МСК
            next_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            wait_seconds = (next_midnight - now).total_seconds()
            await asyncio.sleep(wait_seconds)
            await self.process_daily_reset()

    async def process_daily_reset(self):
        """В 00:00 сбрасывает стрик тем кто не заходил вчера."""
        today = datetime.now(MSK).date()
        yesterday = (today - timedelta(days=1)).isoformat()

        for guild in self.bot.guilds:
            rows = get_all_streaks(guild.id)
            for user_id, streak, last_date in rows:
                # Сбрасываем только тех кто не заходил вчера и не заходил сегодня
                if last_date != yesterday and last_date != today.isoformat():
                    member = guild.get_member(user_id)
                    if not member:
                        continue
                    update_streak(guild.id, user_id, 0, today.isoformat())
                    await self.update_nick(member, 0)
                    log.info(f"[{guild.name}] Стрик сброшен: {member} (последний заход: {last_date})")

            voice_today[guild.id] = set()

    async def update_nick(self, member: disnake.Member, streak: int):
        try:
            current = member.display_name
            new_nick = build_nick(current, streak)
            if new_nick != current:
                await member.edit(nick=new_nick if new_nick != member.name else None)
        except disnake.Forbidden:
            pass

    @commands.Cog.listener()
    async def on_ready(self):
        """При старте восстанавливаем voice_today из БД."""
        today = datetime.now(MSK).date().isoformat()
        for guild in self.bot.guilds:
            rows = get_all_streaks(guild.id)
            voice_today[guild.id] = {
                user_id for user_id, _, last_date in rows
                if last_date == today
            }

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: disnake.Member, before: disnake.VoiceState, after: disnake.VoiceState):
        if member.bot:
            return
        # Зашёл в войс — отмечаем и сразу выдаём стрик если первый раз сегодня
        if not before.channel and after.channel:
            guild_id = member.guild.id
            today = datetime.now(MSK).date().isoformat()

            if guild_id not in voice_today:
                voice_today[guild_id] = set()

            # Если сегодня ещё не заходил — сразу обновляем стрик
            if member.id not in voice_today[guild_id]:
                voice_today[guild_id].add(member.id)
                streak, last_date = get_streak(guild_id, member.id)
                yesterday = (datetime.now(MSK).date() - timedelta(days=1)).isoformat()

                if last_date == today:
                    # Уже заходил сегодня (например после перезапуска бота) — не меняем
                    return
                elif last_date == yesterday:
                    # Заходил вчера — продолжаем стрик
                    new_streak = streak + 1
                else:
                    # Первый раз или пропустил день — начинаем с 1
                    new_streak = 1

                update_streak(guild_id, member.id, new_streak, today)
                await self.update_nick(member, new_streak)
                log.info(f"[{member.guild.name}] Стрик {member}: {streak} -> {new_streak}")


def setup(bot: commands.InteractionBot):
    bot.add_cog(Streaks(bot))
