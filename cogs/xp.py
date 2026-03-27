import disnake
import logging
import random
import time
import asyncio
from disnake.ext import commands
from db.database import add_xp, get_xp, get_leaderboard, set_xp_role, get_xp_role, xp_for_level

log = logging.getLogger("cogs.xp")

# Кулдаун на получение XP за сообщения: user_id -> время последнего сообщения
cooldowns: dict[int, float] = {}

COOLDOWN_SECONDS = 60   # Минимальный интервал между начислением XP за сообщения
XP_MIN, XP_MAX = 10, 25 # Диапазон случайного XP за сообщение
VOICE_INTERVAL = 300    # Интервал начисления XP за войс (5 минут)
VOICE_XP = 10           # XP за каждый интервал в войсе


async def give_xp_and_notify(bot, guild_id: int, user_id: int, amount: int):
    """Начисляет XP участнику и уведомляет о повышении уровня."""
    guild = bot.get_guild(guild_id)
    if not guild:
        return
    member = guild.get_member(user_id)
    if not member:
        return
    xp, level, leveled_up = add_xp(guild_id, user_id, amount)
    if leveled_up:
        log.info(f"[{guild.name}] {member} достиг уровня {level}")
        # Уведомляем в голосовом канале если участник там находится
        if member.voice and member.voice.channel:
            await member.voice.channel.send(
                f"🎉 {member.mention} достиг **{level}** уровня!", delete_after=10
            )
        # Выдаём роль если она привязана к уровню
        role_id = get_xp_role(guild_id, level)
        if role_id:
            role = guild.get_role(role_id)
            if role:
                await member.add_roles(role)
                log.info(f"[{guild.name}] Роль '{role.name}' выдана {member}")


class XP(commands.Cog):
    def __init__(self, bot: commands.InteractionBot):
        self.bot = bot
        self.voice_xp_task = bot.loop.create_task(self.voice_xp_loop())

    def cog_unload(self):
        self.voice_xp_task.cancel()

    async def voice_xp_loop(self):
        """Каждые 5 минут начисляет XP всем кто находится в войсе."""
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            await asyncio.sleep(VOICE_INTERVAL)
            try:
                from cogs.tracker import voice_sessions
                now = time.time()
                # Чистим устаревшие cooldowns чтобы не копить память
                expired = [uid for uid, t in cooldowns.items() if now - t > COOLDOWN_SECONDS * 2]
                for uid in expired:
                    del cooldowns[uid]
                # Начисляем XP всем кто в войсе достаточно долго
                for guild in self.bot.guilds:
                    sessions = voice_sessions.get(guild.id, {})
                    for user_id, join_time in list(sessions.items()):
                        if now - join_time >= VOICE_INTERVAL:
                            sessions[user_id] = now
                            await give_xp_and_notify(self.bot, guild.id, user_id, VOICE_XP)
                            log.info(f"[{guild.name}] Голосовой XP +{VOICE_XP} -> ID:{user_id}")
            except Exception as e:
                log.error(f"Ошибка в voice_xp_loop: {e}")

    @commands.Cog.listener()
    async def on_message(self, message: disnake.Message):
        """Начисляем XP за сообщения с кулдауном."""
        if not message.guild or message.author.bot:
            return
        now = time.time()
        last = cooldowns.get(message.author.id, 0)
        # Пропускаем если кулдаун ещё не истёк
        if now - last < COOLDOWN_SECONDS:
            return
        cooldowns[message.author.id] = now
        amount = random.randint(XP_MIN, XP_MAX)
        await give_xp_and_notify(self.bot, message.guild.id, message.author.id, amount)

    @commands.slash_command(description="Посмотреть свой уровень и XP")
    async def rank(self, inter: disnake.ApplicationCommandInteraction, member: disnake.Member = None):
        member = member or inter.author
        xp, level = get_xp(inter.guild.id, member.id)
        needed = xp_for_level(level + 1)
        embed = disnake.Embed(title=f"⭐ {member.display_name}", color=disnake.Color.gold())
        embed.add_field(name="Уровень", value=str(level), inline=True)
        embed.add_field(name="XP", value=f"{xp} / {needed}", inline=True)
        embed.set_thumbnail(url=member.display_avatar.url)
        await inter.response.send_message(embed=embed)

    @commands.slash_command(description="Топ участников по XP")
    async def top(self, inter: disnake.ApplicationCommandInteraction):
        rows = get_leaderboard(inter.guild.id)
        if not rows:
            return await inter.response.send_message("Пока никто не набрал XP.", ephemeral=True)
        embed = disnake.Embed(title="🏆 Топ участников", color=disnake.Color.gold())
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, (user_id, xp, level) in enumerate(rows):
            m = inter.guild.get_member(user_id)
            name = m.display_name if m else f"ID:{user_id}"
            prefix = medals[i] if i < 3 else f"{i+1}."
            lines.append(f"{prefix} **{name}** — ур. {level} ({xp} XP)")
        embed.description = "\n".join(lines)
        await inter.response.send_message(embed=embed)

    @commands.slash_command(
        description="Привязать роль к уровню",
        default_member_permissions=disnake.Permissions(administrator=True)
    )
    async def set_level_role(self, inter: disnake.ApplicationCommandInteraction, level: int, role: disnake.Role):
        set_xp_role(inter.guild.id, level, role.id)
        await inter.response.send_message(f"✅ Роль {role.mention} будет выдаваться на {level} уровне.", ephemeral=True)

    @commands.slash_command(
        description="Выдать XP участнику вручную",
        default_member_permissions=disnake.Permissions(administrator=True)
    )
    async def give_xp(self, inter: disnake.ApplicationCommandInteraction, member: disnake.Member, amount: int):
        if amount <= 0:
            return await inter.response.send_message("❌ Укажи положительное число.", ephemeral=True)
        xp, level, leveled_up = add_xp(inter.guild.id, member.id, amount)
        log.info(f"[{inter.guild.name}] {inter.author} выдал {amount} XP -> {member}")
        msg = f"✅ {member.mention} получил **{amount} XP**. Уровень: {level}"
        if leveled_up:
            msg += " (повышение уровня!)"
            role_id = get_xp_role(inter.guild.id, level)
            if role_id:
                role = inter.guild.get_role(role_id)
                if role:
                    await member.add_roles(role)
        await inter.response.send_message(msg, ephemeral=True)


def setup(bot: commands.InteractionBot):
    bot.add_cog(XP(bot))
