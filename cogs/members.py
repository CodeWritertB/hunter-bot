import disnake
import logging
from disnake.ext import commands
from db.database import get_channel, set_channel

log = logging.getLogger("cogs.members")


class Members(commands.Cog):
    def __init__(self, bot: commands.InteractionBot):
        self.bot = bot

    @commands.slash_command(
        description="Установить канал для оповещений о входе/выходе участников",
        default_member_permissions=disnake.Permissions(administrator=True)
    )
    async def set_info(self, inter: disnake.ApplicationCommandInteraction, channel: disnake.TextChannel):
        set_channel(inter.guild.id, channel.id)
        log.info(f"[{inter.guild.name}] Канал оповещений установлен: #{channel.name} ({inter.author})")
        await inter.response.send_message(f"✅ Канал оповещений установлен: {channel.mention}", ephemeral=True)

    @commands.Cog.listener()
    async def on_member_join(self, member: disnake.Member):
        # Отправляем приветствие в назначенный канал
        log.info(f"[{member.guild.name}] Участник зашёл: {member} ({member.id})")
        channel_id = get_channel(member.guild.id)
        if not channel_id:
            return
        channel = self.bot.get_channel(channel_id)
        if channel:
            await channel.send(f"👋 {member.mention} присоединился к серверу!")

    @commands.Cog.listener()
    async def on_member_remove(self, member: disnake.Member):
        # Отправляем уведомление об уходе участника
        log.info(f"[{member.guild.name}] Участник вышел: {member} ({member.id})")
        channel_id = get_channel(member.guild.id)
        if not channel_id:
            return
        channel = self.bot.get_channel(channel_id)
        if channel:
            await channel.send(f"👋 {member.mention} покинул сервер.")


def setup(bot: commands.InteractionBot):
    bot.add_cog(Members(bot))
