import disnake
import logging
from disnake.ext import commands
from db.database import (
    get_log_channel, get_admin_log_channel,
    set_log_channel, set_admin_log_channel,
    is_temp_channel
)

log = logging.getLogger("cogs.logger")


async def get_audit_user(guild: disnake.Guild, action: disnake.AuditLogAction, target_id: int = None):
    try:
        async for entry in guild.audit_logs(limit=1, action=action):
            if target_id is None or (entry.target and entry.target.id == target_id):
                return entry.user
    except Exception:
        pass
    return None


async def send_log(bot, guild_id: int, embed: disnake.Embed, admin: bool = False):
    channel_id = get_admin_log_channel(guild_id) if admin else get_log_channel(guild_id)
    if not channel_id:
        return
    channel = bot.get_channel(channel_id)
    if channel:
        await channel.send(embed=embed)


class Logger(commands.Cog):
    def __init__(self, bot: commands.InteractionBot):
        self.bot = bot

    @commands.slash_command(
        description="Назначить канал для логов сервера",
        default_member_permissions=disnake.Permissions(administrator=True)
    )
    async def set_log(self, inter: disnake.ApplicationCommandInteraction, channel: disnake.TextChannel):
        set_log_channel(inter.guild.id, channel.id)
        await inter.response.send_message(f"✅ Канал логов: {channel.mention}", ephemeral=True)

    @commands.slash_command(
        description="Назначить канал для логов админских действий",
        default_member_permissions=disnake.Permissions(administrator=True)
    )
    async def set_admin_log(self, inter: disnake.ApplicationCommandInteraction, channel: disnake.TextChannel):
        set_admin_log_channel(inter.guild.id, channel.id)
        await inter.response.send_message(f"✅ Канал админ-логов: {channel.mention}", ephemeral=True)

    # --- Messages ---

    @commands.Cog.listener()
    async def on_message_delete(self, message: disnake.Message):
        if not message.guild or message.author.bot:
            return
        executor = await get_audit_user(message.guild, disnake.AuditLogAction.message_delete, message.author.id)
        embed = disnake.Embed(title="Сообщение удалено", color=disnake.Color.red())
        embed.add_field(name="Автор", value=message.author.mention, inline=True)
        embed.add_field(name="Канал", value=message.channel.mention, inline=True)
        if executor and executor.id != message.author.id:
            embed.add_field(name="Удалил", value=executor.mention, inline=True)
        embed.add_field(name="Текст", value=message.content[:1024] or "*пусто*", inline=False)
        embed.set_footer(text=f"ID автора: {message.author.id}")
        await send_log(self.bot, message.guild.id, embed)

    @commands.Cog.listener()
    async def on_message_edit(self, before: disnake.Message, after: disnake.Message):
        if not before.guild or before.author.bot or before.content == after.content:
            return
        embed = disnake.Embed(title="Сообщение изменено", color=disnake.Color.yellow())
        embed.add_field(name="Автор", value=before.author.mention, inline=True)
        embed.add_field(name="Канал", value=before.channel.mention, inline=True)
        embed.add_field(name="До", value=before.content[:1024] or "*пусто*", inline=False)
        embed.add_field(name="После", value=after.content[:1024] or "*пусто*", inline=False)
        embed.set_footer(text=f"ID: {before.author.id}")
        await send_log(self.bot, before.guild.id, embed)

    # --- Members ---

    @commands.Cog.listener()
    async def on_member_update(self, before: disnake.Member, after: disnake.Member):
        if before.roles == after.roles and before.nick == after.nick:
            return
        executor = await get_audit_user(after.guild, disnake.AuditLogAction.member_update, after.id)
        embed = disnake.Embed(title="Участник обновлён", color=disnake.Color.blurple())
        embed.add_field(name="Участник", value=after.mention, inline=True)
        if executor and executor.id != after.id:
            embed.add_field(name="Кем", value=executor.mention, inline=True)
        if before.nick != after.nick:
            embed.add_field(name="Ник", value=f"{before.nick} -> {after.nick}", inline=False)
        if before.roles != after.roles:
            added = [r.mention for r in after.roles if r not in before.roles]
            removed = [r.mention for r in before.roles if r not in after.roles]
            if added:
                embed.add_field(name="Роли добавлены", value=" ".join(added), inline=False)
            if removed:
                embed.add_field(name="Роли убраны", value=" ".join(removed), inline=False)
        embed.set_footer(text=f"ID: {after.id}")
        await send_log(self.bot, after.guild.id, embed, admin=True)

    @commands.Cog.listener()
    async def on_member_remove(self, member: disnake.Member):
        executor = await get_audit_user(member.guild, disnake.AuditLogAction.kick, member.id)
        title = "Участник кикнут" if executor and executor.id != member.id else "Участник покинул сервер"
        embed = disnake.Embed(title=title, color=disnake.Color.orange())
        embed.add_field(name="Участник", value=f"{member.mention} ({member})", inline=False)
        if executor and executor.id != member.id:
            embed.add_field(name="Кикнул", value=executor.mention, inline=True)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"ID: {member.id}")
        await send_log(self.bot, member.guild.id, embed, admin=bool(executor and executor.id != member.id))

    # --- Channels ---

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: disnake.abc.GuildChannel):
        if isinstance(channel, disnake.VoiceChannel) and is_temp_channel(channel.id):
            return
        executor = await get_audit_user(channel.guild, disnake.AuditLogAction.channel_create, channel.id)
        embed = disnake.Embed(title="Канал создан", color=disnake.Color.green())
        embed.add_field(name="Канал", value=f"#{channel.name}", inline=True)
        embed.add_field(name="Тип", value=str(channel.type), inline=True)
        if executor:
            embed.add_field(name="Кем", value=executor.mention, inline=True)
        await send_log(self.bot, channel.guild.id, embed, admin=True)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: disnake.abc.GuildChannel):
        if isinstance(channel, disnake.VoiceChannel) and is_temp_channel(channel.id):
            return
        executor = await get_audit_user(channel.guild, disnake.AuditLogAction.channel_delete)
        embed = disnake.Embed(title="Канал удалён", color=disnake.Color.red())
        embed.add_field(name="Канал", value=f"#{channel.name}", inline=True)
        embed.add_field(name="Тип", value=str(channel.type), inline=True)
        if executor:
            embed.add_field(name="Кем", value=executor.mention, inline=True)
        await send_log(self.bot, channel.guild.id, embed, admin=True)

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before: disnake.abc.GuildChannel, after: disnake.abc.GuildChannel):
        if before.name == after.name:
            return
        if isinstance(after, disnake.VoiceChannel) and is_temp_channel(after.id):
            return
        executor = await get_audit_user(after.guild, disnake.AuditLogAction.channel_update, after.id)
        embed = disnake.Embed(title="Канал переименован", color=disnake.Color.yellow())
        embed.add_field(name="До", value=f"#{before.name}", inline=True)
        embed.add_field(name="После", value=f"#{after.name}", inline=True)
        if executor:
            embed.add_field(name="Кем", value=executor.mention, inline=True)
        await send_log(self.bot, after.guild.id, embed, admin=True)

    # --- Roles ---

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: disnake.Role):
        executor = await get_audit_user(role.guild, disnake.AuditLogAction.role_create, role.id)
        embed = disnake.Embed(title="Роль создана", color=disnake.Color.green())
        embed.add_field(name="Роль", value=role.mention, inline=True)
        if executor:
            embed.add_field(name="Кем", value=executor.mention, inline=True)
        await send_log(self.bot, role.guild.id, embed, admin=True)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: disnake.Role):
        executor = await get_audit_user(role.guild, disnake.AuditLogAction.role_delete)
        embed = disnake.Embed(title="Роль удалена", color=disnake.Color.red())
        embed.add_field(name="Роль", value=f"@{role.name}", inline=True)
        if executor:
            embed.add_field(name="Кем", value=executor.mention, inline=True)
        await send_log(self.bot, role.guild.id, embed, admin=True)

    # --- Bans ---

    @commands.Cog.listener()
    async def on_member_ban(self, guild: disnake.Guild, user: disnake.User):
        executor = await get_audit_user(guild, disnake.AuditLogAction.ban, user.id)
        embed = disnake.Embed(title="Участник забанен", color=disnake.Color.dark_red())
        embed.add_field(name="Пользователь", value=f"{user.mention} ({user})", inline=True)
        if executor:
            embed.add_field(name="Кем", value=executor.mention, inline=True)
        embed.set_footer(text=f"ID: {user.id}")
        await send_log(self.bot, guild.id, embed, admin=True)

    @commands.Cog.listener()
    async def on_member_unban(self, guild: disnake.Guild, user: disnake.User):
        executor = await get_audit_user(guild, disnake.AuditLogAction.unban, user.id)
        embed = disnake.Embed(title="Участник разбанен", color=disnake.Color.green())
        embed.add_field(name="Пользователь", value=f"{user.mention} ({user})", inline=True)
        if executor:
            embed.add_field(name="Кем", value=executor.mention, inline=True)
        embed.set_footer(text=f"ID: {user.id}")
        await send_log(self.bot, guild.id, embed, admin=True)

    # --- Voice ---

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: disnake.Member, before: disnake.VoiceState, after: disnake.VoiceState):
        if before.channel == after.channel:
            return
        embed = disnake.Embed(color=disnake.Color.blurple())
        if not before.channel and after.channel:
            embed.title = "Зашёл в голосовой канал"
            embed.add_field(name="Участник", value=member.mention, inline=True)
            embed.add_field(name="Канал", value=after.channel.mention, inline=True)
        elif before.channel and not after.channel:
            embed.title = "Вышел из голосового канала"
            embed.add_field(name="Участник", value=member.mention, inline=True)
            embed.add_field(name="Канал", value=before.channel.mention, inline=True)
        else:
            embed.title = "Сменил голосовой канал"
            embed.add_field(name="Участник", value=member.mention, inline=True)
            embed.add_field(name="Откуда", value=before.channel.mention, inline=True)
            embed.add_field(name="Куда", value=after.channel.mention, inline=True)
        embed.set_footer(text=f"ID: {member.id}")
        await send_log(self.bot, member.guild.id, embed)


def setup(bot: commands.InteractionBot):
    bot.add_cog(Logger(bot))
