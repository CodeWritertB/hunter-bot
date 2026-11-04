import disnake
import logging
import asyncio
import re
from datetime import date, datetime, timezone, timedelta
from disnake.ext import commands
from db.database import update_streak, get_streak

log = logging.getLogger("cogs.streaks")
MSK = timezone(timedelta(hours=3))


def eligible(member):
    return not member.bot and member.id != member.guild.owner_id


def build_nick(base_nick: str, streak: int, cold_streak: int = 0) -> str:
    """Строит ник с суффиксом стрика или обратного стрика."""
    # Убираем старые суффиксы с любым количеством эмодзи и цифр в конце
    base_nick = re.sub(r'\s*(?:[\U0001F525\u2744]\uFE0F?\s*)+\d+$', '', base_nick).strip()
    if streak >= 1:
        return f"{base_nick} 🔥{streak}"
    elif cold_streak >= 1:
        return f"{base_nick} ❄️{cold_streak}"
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
            today = datetime.now(MSK).date()
            for guild in self.bot.guilds:
                for member in guild.members:
                    if eligible(member):
                        await self.sync_member(member, datetime.now(MSK).date(),
                                               bool(member.voice and member.voice.channel))
            midnight = datetime.combine(today + timedelta(days=1), datetime.min.time(), MSK)
            await asyncio.sleep(max(0, (midnight - datetime.now(MSK)).total_seconds()))

    async def sync_member(self, member, today, active):
        guild_id = member.guild.id
        streak, cold, last_date = get_streak(guild_id, member.id)
        previous = (streak, cold, last_date)
        yesterday = today - timedelta(days=1)
        if last_date and (streak or cold):
            counted = date.fromisoformat(last_date)
        elif member.joined_at:
            counted = member.joined_at.astimezone(MSK).date()
        else:
            counted = yesterday
        missed = (yesterday - counted).days
        if missed > 0:
            streak, cold, last_date = 0, cold + missed, yesterday.isoformat()
        if active and (last_date != today.isoformat() or not streak):
            streak, cold, last_date = streak + 1, 0, today.isoformat()
        if (streak, cold, last_date) != previous:
            update_streak(guild_id, member.id, streak, last_date, cold)
        current = member.display_name
        nickname = build_nick(current, streak, cold)
        if nickname != current:
            try:
                await member.edit(nick=nickname if nickname != member.name else None)
            except disnake.HTTPException:
                log.warning("Не удалось изменить ник участника %s", member.id)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if eligible(member) and after.channel and before.channel != after.channel:
            await self.sync_member(member, datetime.now(MSK).date(), True)

    @commands.slash_command(description="Проверить голосовой стрик участника")
    async def streak(self, inter: disnake.ApplicationCommandInteraction, member: disnake.Member = None):
        member = member or inter.author
        if not eligible(member):
            return await inter.response.send_message("Стрики недоступны ботам и владельцу сервера.", ephemeral=True)
        streak, cold_streak, last_date = get_streak(inter.guild.id, member.id)
        if streak >= 1:
            text = f"🔥 Стрик голосовой активности: {streak}"
        elif cold_streak >= 1:
            text = f"❄️ Обратный стрик: {cold_streak}"
        else:
            text = "Стрик не начат."
        embed = disnake.Embed(
            title=f"Статус стрика — {member.display_name}",
            description=text,
            color=disnake.Color.blurple()
        )
        embed.add_field(name="Последняя дата", value=str(last_date or "нет данных"), inline=False)
        await inter.response.send_message(embed=embed, ephemeral=True)

    @commands.slash_command(description="Применить записанный стрик к никам участника")
    async def refresh_nick(self, inter: disnake.ApplicationCommandInteraction, member: disnake.Member = None):
        member = member or inter.author
        if not eligible(member):
            return await inter.response.send_message("Стрики недоступны ботам и владельцу сервера.", ephemeral=True)
        if member != inter.author and not inter.author.guild_permissions.manage_nicknames:
            return await inter.response.send_message("❌ Нужно право «Управлять никами», чтобы менять чужие имена.", ephemeral=True)

        streak, cold_streak, _ = get_streak(inter.guild.id, member.id)
        new_nick = build_nick(member.display_name, streak, cold_streak)
        if new_nick == member.display_name:
            return await inter.response.send_message("✅ Ник уже соответствует текущему стрику.", ephemeral=True)

        try:
            await member.edit(nick=new_nick if new_nick != member.name else None)
            await inter.response.send_message(f"✅ Ник обновлён: **{new_nick}**", ephemeral=True)
        except disnake.Forbidden:
            await inter.response.send_message("❌ Нет прав менять никнейм.", ephemeral=True)


def setup(bot: commands.InteractionBot):
    bot.add_cog(Streaks(bot))
