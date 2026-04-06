import disnake
import logging
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
    embed.add_field(name="🖥 CS2 сервер", value=ch(_get_server(guild_id, "cs_server_channel_id")), inline=True)
    embed.add_field(name="🎵 Канал музыки", value=ch(_get_server(guild_id, "music_channel_id")), inline=True)

    if verify:
        _, guest_id, member_id = verify
        embed.add_field(name="✅ Верификация", value=f"Гость: {role(guest_id)}\nУчастник: {role(member_id)}", inline=False)
    else:
        embed.add_field(name="✅ Верификация", value="❌ Не настроена", inline=False)

    embed.set_footer(text="Нажми кнопку чтобы изменить настройку")
    return embed


# --- Модальные окна для выбора канала ---

class ChannelModal(disnake.ui.Modal):
    def __init__(self, setting: str, label: str):
        self.setting = setting
        super().__init__(title=f"Настроить: {label}", components=[
            disnake.ui.TextInput(label="ID канала", custom_id="channel_id", placeholder="Скопируй ID канала (ПКМ → Копировать ID)")
        ])

    async def callback(self, inter: disnake.ModalInteraction):
        raw = inter.text_values["channel_id"].strip()
        if not raw.isdigit():
            return await inter.response.send_message("❌ Введи числовой ID канала.", ephemeral=True)
        channel_id = int(raw)
        channel = inter.guild.get_channel(channel_id)
        if not channel:
            return await inter.response.send_message("❌ Канал не найден на этом сервере.", ephemeral=True)

        guild_id = inter.guild.id
        if self.setting == "welcome":
            set_channel(guild_id, channel_id)
        elif self.setting == "lobby":
            set_lobby(guild_id, channel_id)
        elif self.setting == "music":
            set_music_channel(guild_id, channel_id)
        elif self.setting == "log":
            set_log_channel(guild_id, channel_id)
        elif self.setting == "admin_log":
            set_admin_log_channel(guild_id, channel_id)
        else:
            _upsert_server(guild_id, **{f"{self.setting}_channel_id": channel_id})

        log.info(f"[{inter.guild.name}] {inter.author} установил {self.setting} -> #{channel.name}")
        await inter.response.send_message(f"✅ {channel.mention} установлен.", ephemeral=True)


class VerifyModal(disnake.ui.Modal):
    def __init__(self):
        super().__init__(title="Настроить верификацию", components=[
            disnake.ui.TextInput(label="ID канала верификации", custom_id="channel_id"),
            disnake.ui.TextInput(label="ID роли гостя", custom_id="guest_id"),
            disnake.ui.TextInput(label="ID роли участника", custom_id="member_id"),
        ])

    async def callback(self, inter: disnake.ModalInteraction):
        try:
            ch_id = int(inter.text_values["channel_id"])
            guest_id = int(inter.text_values["guest_id"])
            member_id = int(inter.text_values["member_id"])
        except ValueError:
            return await inter.response.send_message("❌ Введи числовые ID.", ephemeral=True)

        channel = inter.guild.get_channel(ch_id)
        guest = inter.guild.get_role(guest_id)
        member_role = inter.guild.get_role(member_id)

        if not channel or not guest or not member_role:
            return await inter.response.send_message("❌ Канал или роль не найдены.", ephemeral=True)

        set_verify_settings(inter.guild.id, ch_id, guest_id, member_id)

        from cogs.verify import VerifyView
        embed = disnake.Embed(
            title="Верификация",
            description="Нажми на кнопку ниже чтобы изменить ник и получить доступ к серверу.\n\nФормат: **Ник (Имя)**\nПример: `CodeWriter (Борис)`",
            color=disnake.Color.blurple()
        )
        await channel.send(embed=embed, view=VerifyView())
        await inter.response.send_message(f"✅ Верификация настроена в {channel.mention}", ephemeral=True)


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
            ("cs_server",   "🖥 CS2 сервер",      _get_server(guild_id, "cs_server_channel_id")),
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
            await inter.response.send_modal(ChannelModal(setting, label))
        return callback

    async def _verify_callback(self, inter: disnake.MessageInteraction):
        await inter.response.send_modal(VerifyModal())


class AdminPanelCog(commands.Cog):
    def __init__(self, bot: commands.InteractionBot):
        self.bot = bot

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
