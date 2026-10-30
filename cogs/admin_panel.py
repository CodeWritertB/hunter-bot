import disnake
import logging
import io
from datetime import datetime, timezone
from disnake.ext import commands
from db.database import (
    _get_server, _upsert_server,
    get_channel, set_channel,
    get_lobby, set_lobby,
    get_music_channel, set_music_channel,
    get_log_channel, set_log_channel,
    get_admin_log_channel, set_admin_log_channel,
    get_verify_settings, set_verify_settings,
)

log = logging.getLogger("cogs.admin_panel")


def build_status_embed(guild: disnake.Guild) -> disnake.Embed:
    """Строит embed с текущими настройками сервера."""
    embed = disnake.Embed(title="⚙️ Панель управления сервером", color=disnake.Color.blurple())

    def ch(channel_id):
        if not channel_id:
            return "❌ Не задан"
        ch = guild.get_channel(channel_id)
        return ch.mention if ch else "⚠️ Канал удалён"

    def role(role_id):
        if not role_id:
            return "❌ Не задана"
        r = guild.get_role(role_id)
        return r.mention if r else "⚠️ Роль удалена"

    guild_id = guild.id
    verify = get_verify_settings(guild_id)

    embed.add_field(name="👋 Оповещения", value=ch(get_channel(guild_id)), inline=True)
    embed.add_field(name="🔊 Лобби комнат", value=ch(get_lobby(guild_id)), inline=True)
    embed.add_field(name="🎵 Музыка", value=ch(get_music_channel(guild_id)), inline=True)
    embed.add_field(name="📋 Лог сервера", value=ch(get_log_channel(guild_id)), inline=True)
    embed.add_field(name="🔧 Админ-лог", value=ch(get_admin_log_channel(guild_id)), inline=True)
    embed.add_field(name="🎮 Dota 2", value=ch(_get_server(guild_id, "dota_channel_id")), inline=True)
    embed.add_field(name="🔫 CS2 матчи", value=ch(_get_server(guild_id, "cs_channel_id")), inline=True)

    if verify:
        _, guest_id, member_id = verify
        embed.add_field(name="✅ Верификация", value=f"Гость: {role(guest_id)}\nУчастник: {role(member_id)}", inline=False)
    else:
        embed.add_field(name="✅ Верификация", value="❌ Не настроена", inline=False)

    embed.set_footer(text="Нажми кнопку чтобы изменить настройку")
    return embed


# --- Модальные окна для выбора канала ---

class ChannelSelect(disnake.ui.ChannelSelect):
    def __init__(self, setting):
        self.setting = setting
        super().__init__(placeholder="Выбери канал", channel_types=[disnake.ChannelType.voice if setting == "lobby" else disnake.ChannelType.text])

    async def callback(self, inter):
        if not inter.author.guild_permissions.administrator:
            return await inter.response.send_message("Нужны права администратора.", ephemeral=True)
        channel_id = self.values[0].id
        setters = {"welcome": set_channel, "lobby": set_lobby, "music": set_music_channel, "log": set_log_channel, "admin_log": set_admin_log_channel}
        if self.setting in setters:
            setters[self.setting](inter.guild.id, channel_id)
        else:
            _upsert_server(inter.guild.id, **{f"{self.setting}_channel_id": channel_id})
        await inter.response.edit_message(content=None, embed=build_status_embed(inter.guild), view=AdminPanelView(inter.guild))
        if self.setting == "music":
            cog = inter.bot.get_cog("Music")
            if cog:
                await cog.ensure_panel(inter.guild)


class VerifyChannelSelect(disnake.ui.ChannelSelect):
    def __init__(self):
        super().__init__(placeholder="1. Канал верификации", channel_types=[disnake.ChannelType.text], row=0)

    async def callback(self, inter):
        self.view.channel_id = self.values[0].id
        await inter.response.edit_message(content=self.view.status(), view=self.view)


class VerifyRoleSelect(disnake.ui.RoleSelect):
    def __init__(self, setting, label, row):
        self.setting = setting
        super().__init__(placeholder=label, row=row)

    async def callback(self, inter):
        role = inter.guild.get_role(self.values[0].id)
        if not role or role.is_default() or role.managed or role >= inter.guild.me.top_role:
            return await inter.response.send_message("Выбери обычную роль ниже высшей роли бота.", ephemeral=True)
        setattr(self.view, self.setting, role.id)
        await inter.response.edit_message(content=self.view.status(), view=self.view)


class VerifySettingsView(disnake.ui.View):
    def __init__(self, guild):
        super().__init__(timeout=300)
        current = get_verify_settings(guild.id) or (None, None, None)
        self.channel_id, self.guest_id, self.member_id = current
        self.add_item(VerifyChannelSelect())
        self.add_item(VerifyRoleSelect("guest_id", "2. Роль гостя", 1))
        self.add_item(VerifyRoleSelect("member_id", "3. Роль участника", 2))

    async def interaction_check(self, inter):
        if not inter.author.guild_permissions.administrator:
            await inter.response.send_message("Нужны права администратора.", ephemeral=True)
            return False
        return True

    def status(self):
        channel = f"<#{self.channel_id}>" if self.channel_id else "не выбран"
        guest = f"<@&{self.guest_id}>" if self.guest_id else "не выбрана"
        member = f"<@&{self.member_id}>" if self.member_id else "не выбрана"
        return f"Настройка верификации\nКанал: {channel}\nГость: {guest}\nУчастник: {member}\nВыбери значения и нажми «Сохранить»."

    @disnake.ui.button(label="Сохранить", style=disnake.ButtonStyle.success, row=3)
    async def save(self, button, inter):
        channel = inter.guild.get_channel(self.channel_id or 0)
        guest = inter.guild.get_role(self.guest_id or 0)
        member = inter.guild.get_role(self.member_id or 0)
        if not isinstance(channel, disnake.TextChannel) or not guest or not member:
            return await inter.response.send_message("Выбери канал и обе роли.", ephemeral=True)
        if guest == member:
            return await inter.response.send_message("Роли гостя и участника должны различаться.", ephemeral=True)
        if not inter.guild.me.guild_permissions.manage_roles or any(r.is_default() or r.managed or r >= inter.guild.me.top_role for r in (guest, member)):
            return await inter.response.send_message("Боту нужно право управления ролями; выбранные роли должны быть ниже его роли.", ephemeral=True)
        await inter.response.defer()
        from cogs.verify import VerifyView
        embed = disnake.Embed(title="Верификация", description="Нажми кнопку, чтобы изменить ник и получить доступ к серверу.\nФормат: **Ник (Имя)**", color=disnake.Color.blurple())
        try:
            await channel.send(embed=embed, view=VerifyView())
        except disnake.HTTPException:
            return await inter.followup.send("Не удалось отправить панель. Проверь права бота в выбранном канале.", ephemeral=True)
        set_verify_settings(inter.guild.id, channel.id, guest.id, member.id)
        await inter.edit_original_response(content=None, embed=build_status_embed(inter.guild), view=AdminPanelView(inter.guild))

    @disnake.ui.button(label="Назад", style=disnake.ButtonStyle.secondary, row=3)
    async def back(self, button, inter):
        await inter.response.edit_message(content=None, embed=build_status_embed(inter.guild), view=AdminPanelView(inter.guild))


# --- Главная панель ---

class AdminPanelView(disnake.ui.View):
    def __init__(self, guild: disnake.Guild):
        super().__init__(timeout=120)
        self.guild = guild
        self._build_buttons()

    def _build_buttons(self):
        guild_id = self.guild.id
        settings = [
            ("welcome",     "👋 Оповещения",    get_channel(guild_id)),
            ("lobby",       "🔊 Лобби комнат",  get_lobby(guild_id)),
            ("music",       "🎵 Музыка",         get_music_channel(guild_id)),
            ("log",         "📋 Лог сервера",    get_log_channel(guild_id)),
            ("admin_log",   "🔧 Админ-лог",      get_admin_log_channel(guild_id)),
            ("dota",        "🎮 Dota 2",          _get_server(guild_id, "dota_channel_id")),
            ("cs",          "🔫 CS2 матчи",       _get_server(guild_id, "cs_channel_id")),
        ]
        for setting, label, current in settings:
            style = disnake.ButtonStyle.success if current else disnake.ButtonStyle.secondary
            btn = disnake.ui.Button(label=label, style=style, custom_id=f"admin_{setting}")
            btn.callback = self._make_callback(setting, label)
            self.add_item(btn)

        # Верификация отдельно
        verify = get_verify_settings(guild_id)
        verify_btn = disnake.ui.Button(
            label="✅ Верификация",
            style=disnake.ButtonStyle.success if verify else disnake.ButtonStyle.secondary,
            custom_id="admin_verify"
        )
        verify_btn.callback = self._verify_callback
        self.add_item(verify_btn)

    def _make_callback(self, setting: str, label: str):
        async def callback(inter: disnake.MessageInteraction):
            view = disnake.ui.View(timeout=120)
            view.add_item(ChannelSelect(setting))
            await inter.response.edit_message(content=f"Выбери канал: {label}", view=view)
        return callback

    async def _verify_callback(self, inter: disnake.MessageInteraction):
        view = VerifySettingsView(inter.guild)
        await inter.response.edit_message(content=view.status(), embed=None, view=view)


class AdminPanelCog(commands.Cog):
    def __init__(self, bot: commands.InteractionBot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        from cogs.server_setup import apply_setup
        for guild in self.bot.guilds:
            try:
                await apply_setup(guild)
            except Exception:
                log.exception("Не удалось завершить согласованную настройку сервера")

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload):
        from cogs.server_setup import apply_setup, GUILD_ID
        if payload.guild_id == GUILD_ID and payload.channel_id in (1487101267624067233, 1487112222571499632, 1486685174459662459):
            guild = self.bot.get_guild(payload.guild_id)
            if guild:
                try:
                    await apply_setup(guild)
                except Exception:
                    log.exception("Не удалось восстановить игровую панель")

    @commands.slash_command(description="Проверить подключение и YouTube-поиск Lavalink",
                            default_member_permissions=disnake.Permissions(administrator=True))
    async def lavalink_test(self, inter):
        if not inter.guild or not inter.author.guild_permissions.administrator:
            return await inter.response.send_message("Нужны права администратора.", ephemeral=True)
        await inter.response.defer(ephemeral=True)
        from cogs.lavalink_probe import probe
        await inter.edit_original_response(content=await probe())

    @commands.slash_command(description="Выгрузить настройки сервера в Markdown для аудита",
                            default_member_permissions=disnake.Permissions(administrator=True),
                            dm_permission=False)
    async def server_audit(self, inter: disnake.ApplicationCommandInteraction):
        if not inter.guild or not inter.author.guild_permissions.administrator:
            return await inter.response.send_message("Нужны права администратора.", ephemeral=True)
        await inter.response.defer(ephemeral=True)
        guild = inter.guild
        def clean(value):
            return str(value).replace("\r", " ").replace("\n", " ").replace("`", "'")
        def perms(value):
            return ", ".join(name for name, enabled in value if enabled) or "нет"
        lines = [f"# Аудит сервера: {clean(guild.name)}",
                 f"Дата UTC: {datetime.now(timezone.utc).isoformat()}",
                 f"ID: {guild.id}; участников: {guild.member_count}",
                 f"Уровень верификации: {guild.verification_level}",
                 f"Фильтр контента: {guild.explicit_content_filter}",
                 f"Уведомления: {guild.default_notifications}; MFA: {guild.mfa_level}",
                 f"Функции: {', '.join(guild.features)}",
                 f"AFK: {guild.afk_channel}; таймаут: {guild.afk_timeout}",
                 f"Системный канал: {guild.system_channel}; правила: {guild.rules_channel}",
                 f"Канал обновлений: {guild.public_updates_channel}",
                 f"Системные уведомления: {guild.system_channel_flags}",
                 "", "## Настройки бота"]
        from db.database import get_all_temp_channels
        for field in build_status_embed(guild).fields:
            lines.append(f"- {field.name}: {clean(field.value)}")
        lines += ["", "## Временные комнаты (ID канала → ID владельца)"]
        lines.extend(f"- {channel_id} → {owner_id}" for channel_id, owner_id in get_all_temp_channels(guild.id))
        lines += ["", "## Роли (сверху вниз)"]
        for role in reversed(guild.roles):
            lines += [f"### {clean(role.name)} ({role.id})",
                      f"Позиция: {role.position}; цвет: {role.color}; отдельно: {role.hoist}; упоминание: {role.mentionable}; управляемая: {role.managed}",
                      f"Права: {perms(role.permissions)}"]
        lines += ["", "## Каналы и категории"]
        for channel in sorted(guild.channels, key=lambda c: (c.category.position if c.category else c.position, c.position, c.id)):
            lines += [f"### {clean(channel.name)} ({channel.id})",
                      f"Тип: {channel.type}; позиция: {channel.position}; категория: {channel.category_id}",
                      f"Синхронизация прав: {getattr(channel, 'permissions_synced', None)}"]
            for attr in ("topic", "nsfw", "slowmode_delay", "bitrate", "user_limit", "rtc_region", "default_auto_archive_duration"):
                if hasattr(channel, attr):
                    lines.append(f"{attr}: {clean(getattr(channel, attr))}")
            for target, overwrite in channel.overwrites.items():
                allow, deny = overwrite.pair()
                lines.append(f"- Overwrite {type(target).__name__} {clean(target.name)} ({target.id}): разрешено [{perms(allow)}]; запрещено [{perms(deny)}]")
            lines.append(f"Эффективные права @everyone: {perms(channel.permissions_for(guild.default_role))}")
            if guild.me:
                lines.append(f"Эффективные права бота: {perms(channel.permissions_for(guild.me))}")
        lines += ["", "## Права ботов (кэш Discord)"]
        for member in guild.members:
            if member.bot:
                lines.append(f"- {clean(member.display_name)} ({member.id}): роли {', '.join(str(r.id) for r in member.roles)}; права {perms(member.guild_permissions)}")
        lines += ["", "## Ограничения отчёта",
                  "Снимок настроек, доступных боту. Не включает сообщения, содержимое личных каналов, токены, приглашения, AutoMod и настройки интеграций.",
                  "Список ботов зависит от кэша. Права участников зависят от сочетания ролей и персональных запретов; administrator обходит запреты каналов."]
        payload = "\n\n".join(lines).encode("utf-8")
        if len(payload) > guild.filesize_limit:
            return await inter.edit_original_response(content="Отчёт превышает лимит файла сервера.")
        await inter.edit_original_response(content="Отчёт готов. Перед передачей проверь названия и описания приватных каналов.",
                                           file=disnake.File(io.BytesIO(payload), filename=f"server-audit-{guild.id}.md"))

    @commands.slash_command(
        description="Открыть панель управления сервером",
        default_member_permissions=disnake.Permissions(administrator=True)
    )
    async def admin(self, inter: disnake.ApplicationCommandInteraction):
        embed = build_status_embed(inter.guild)
        view = AdminPanelView(inter.guild)
        await inter.response.send_message(embed=embed, view=view, ephemeral=True)


def setup(bot: commands.InteractionBot):
    bot.add_cog(AdminPanelCog(bot))
