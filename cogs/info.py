import disnake
import logging
from disnake.ext import commands
from db.database import get_xp, get_member_stats, get_server_stats, get_top_members

log = logging.getLogger("cogs.info")


class Info(commands.Cog):
    def __init__(self, bot: commands.InteractionBot):
        self.bot = bot

    @commands.slash_command(
        description="Информация об участнике",
        default_member_permissions=disnake.Permissions(administrator=True)
    )
    async def userinfo(self, inter: disnake.ApplicationCommandInteraction, member: disnake.Member = None):
        member = member or inter.author
        xp, level = get_xp(inter.guild.id, member.id)
        stats = get_member_stats(inter.guild.id, member.id)
        roles = [r.mention for r in member.roles if r.name != "@everyone"]

        embed = disnake.Embed(title=f"👤 {member}", color=member.color)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="ID", value=str(member.id), inline=True)
        embed.add_field(name="Ник", value=member.display_name, inline=True)
        embed.add_field(name="Бот", value="Да" if member.bot else "Нет", inline=True)
        embed.add_field(name="Аккаунт создан", value=disnake.utils.format_dt(member.created_at, "D"), inline=True)
        embed.add_field(name="Зашёл на сервер", value=disnake.utils.format_dt(member.joined_at, "D") if member.joined_at else "—", inline=True)
        embed.add_field(name="Уровень / XP", value=f"{level} / {xp}", inline=True)
        if stats:
            embed.add_field(name="Сообщений", value=str(stats[3]), inline=True)
            voice_h = stats[4] // 60
            voice_m = stats[4] % 60
            embed.add_field(name="Время в войсе", value=f"{voice_h}ч {voice_m}м", inline=True)
            if stats[5]:
                try:
                    last = disnake.utils.format_dt(
                        disnake.utils.parse_time(stats[5].replace("+00:00", "+00:00")), "R"
                    )
                    embed.add_field(name="Последняя активность", value=last, inline=True)
                except Exception:
                    pass
        embed.add_field(name=f"Роли ({len(roles)})", value=" ".join(roles) if roles else "Нет", inline=False)
        await inter.response.send_message(embed=embed)

    @commands.slash_command(
        description="Информация о сервере",
        default_member_permissions=disnake.Permissions(administrator=True)
    )
    async def serverinfo(self, inter: disnake.ApplicationCommandInteraction):
        guild = inter.guild
        bots = sum(1 for m in guild.members if m.bot)
        humans = guild.member_count - bots
        online = sum(1 for m in guild.members if m.status != disnake.Status.offline and not m.bot)

        stats = get_server_stats(guild.id)
        top = get_top_members(guild.id, 5)

        embed = disnake.Embed(title=f"🏠 {guild.name}", color=disnake.Color.blurple())
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)

        embed.add_field(name="ID", value=str(guild.id), inline=True)
        embed.add_field(name="Владелец", value=guild.owner.mention if guild.owner else "—", inline=True)
        embed.add_field(name="Создан", value=disnake.utils.format_dt(guild.created_at, "D"), inline=True)
        embed.add_field(name="Участники", value=f"👤 {humans} / 🤖 {bots} / 🟢 {online} онлайн", inline=True)
        embed.add_field(name="Каналы", value=f"💬 {len(guild.text_channels)} / 🔊 {len(guild.voice_channels)}", inline=True)
        embed.add_field(name="Роли", value=str(len(guild.roles)), inline=True)
        embed.add_field(name="Буст", value=f"Уровень {guild.premium_tier} ({guild.premium_subscription_count} бустов)", inline=True)

        if stats:
            total_msg, total_voice, peak, peak_date = stats
            voice_h = (total_voice or 0) // 60
            voice_m = (total_voice or 0) % 60
            embed.add_field(name="Всего сообщений", value=str(total_msg or 0), inline=True)
            embed.add_field(name="Время в войсе", value=f"{voice_h}ч {voice_m}м", inline=True)
            if peak:
                peak_fmt = disnake.utils.format_dt(
                    disnake.utils.parse_time(peak_date.replace("+00:00", "+00:00")), "D"
                ) if peak_date else "—"
                embed.add_field(name="Пик онлайна", value=f"{peak} чел. ({peak_fmt})", inline=True)

        if top:
            lines = []
            for uid, msgs, voice_min in top:
                m = guild.get_member(uid)
                name = m.display_name if m else f"ID:{uid}"
                lines.append(f"**{name}** — {msgs} сообщ. / {voice_min // 60}ч {voice_min % 60}м войс")
            embed.add_field(name="Топ активных", value="\n".join(lines), inline=False)

        await inter.response.send_message(embed=embed)


    @commands.slash_command(
        description="Очистить сообщения в канале",
        default_member_permissions=disnake.Permissions(administrator=True)
    )
    async def clear(self, inter: disnake.ApplicationCommandInteraction, amount: int = 100):
        """Удаляет до 100 сообщений в текущем канале."""
        if amount < 1 or amount > 100:
            return await inter.response.send_message("❌ Укажи число от 1 до 100.", ephemeral=True)
        await inter.response.defer(ephemeral=True)
        deleted = await inter.channel.purge(limit=amount)
        log.info(f"[{inter.guild.name}] {inter.author} очистил {len(deleted)} сообщений в #{inter.channel.name}")
        await inter.edit_original_response(content=f"✅ Удалено {len(deleted)} сообщений.")

    @commands.slash_command(
        description="Отправить сообщение участнику от имени бота",
        default_member_permissions=disnake.Permissions(administrator=True)
    )
    async def send_message(self, inter: disnake.ApplicationCommandInteraction, member: disnake.Member, text: str):
        if member.bot:
            return await inter.response.send_message("❌ Нельзя отправить сообщение боту.", ephemeral=True)
        try:
            embed = disnake.Embed(description=text, color=disnake.Color.blurple())
            embed.set_footer(text=f"Сообщение от {inter.guild.name}")
            await member.send(embed=embed)
            log.info(f"[{inter.guild.name}] {inter.author} отправил сообщение -> {member}: {text}")
            await inter.response.send_message(f"✅ Сообщение отправлено {member.mention}", ephemeral=True)
        except disnake.Forbidden:
            await inter.response.send_message("❌ Не удалось отправить — у участника закрыты ЛС.", ephemeral=True)


def setup(bot: commands.InteractionBot):
    bot.add_cog(Info(bot))
