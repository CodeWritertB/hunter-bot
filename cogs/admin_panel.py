import io
import json
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


def permission_names(permissions: disnake.Permissions) -> list[str]:
    return [name for name, enabled in permissions if enabled]


def build_server_audit(guild: disnake.Guild) -> dict:
    roles = [
        {
            "id": role.id,
            "name": role.name,
            "position": role.position,
            "color": f"#{role.color.value:06X}",
            "hoist": role.hoist,
            "mentionable": role.mentionable,
            "managed": role.managed,
            "permissions": permission_names(role.permissions),
        }
        for role in sorted(guild.roles, key=lambda item: item.position, reverse=True)
    ]

    channels = []
    for channel in sorted(guild.channels, key=lambda item: (item.position, item.id)):
        overwrites = []
        for target, overwrite in channel.overwrites.items():
            allow, deny = overwrite.pair()
            target_type = "role" if isinstance(target, disnake.Role) else "member"
            overwrites.append({
                "target_type": target_type,
                "target_id": target.id,
                "target_name": str(target),
                "allow": permission_names(allow),
                "deny": permission_names(deny),
            })

        entry = {
            "id": channel.id,
            "name": channel.name,
            "type": str(channel.type),
            "position": channel.position,
            "category_id": getattr(channel, "category_id", None),
            "overwrites": overwrites,
        }
        if isinstance(channel, disnake.TextChannel):
            entry.update({
                "topic": channel.topic,
                "nsfw": channel.nsfw,
                "slowmode_seconds": channel.slowmode_delay,
            })
        elif isinstance(channel, disnake.VoiceChannel):
            entry.update({
                "user_limit": channel.user_limit,
                "bitrate": channel.bitrate,
                "rtc_region": channel.rtc_region,
            })
        channels.append(entry)

    return {
        "guild": {
            "id": guild.id,
            "name": guild.name,
            "owner_id": guild.owner_id,
            "verification_level": str(guild.verification_level),
            "explicit_content_filter": str(guild.explicit_content_filter),
            "default_role_id": guild.default_role.id,
            "system_channel_id": guild.system_channel.id if guild.system_channel else None,
            "rules_channel_id": getattr(guild, "rules_channel_id", None),
            "public_updates_channel_id": getattr(guild, "public_updates_channel_id", None),
        },
        "roles": roles,
        "channels": channels,
    }


HUNT_RULES_MARKER = "Hunter Bot • Hunt setup: rules"
HUNT_WELCOME_MARKER = "Hunter Bot • Hunt setup: welcome"


def _find_category(guild: disnake.Guild, aliases: tuple[str, ...]) -> disnake.CategoryChannel | None:
    names = {name.casefold() for name in aliases}
    return next((category for category in guild.categories if category.name.casefold() in names), None)


def _find_channel(guild: disnake.Guild, aliases: tuple[str, ...]) -> disnake.abc.GuildChannel | None:
    names = {name.casefold() for name in aliases}
    return next(
        (
            channel for channel in guild.channels
            if not isinstance(channel, disnake.CategoryChannel) and channel.name.casefold() in names
        ),
        None,
    )


async def _upsert_setup_embed(
    channel: disnake.TextChannel,
    embed: disnake.Embed,
    marker: str,
    bot_user_id: int,
) -> None:
    async for message in channel.history(limit=100):
        if message.author.id == bot_user_id and any(
            item.footer and item.footer.text == marker for item in message.embeds
        ):
            await message.edit(embed=embed)
            return
    await channel.send(embed=embed)


async def apply_hunt_server_setup(guild: disnake.Guild) -> tuple[list[str], list[str]]:
    changes: list[str] = []
    skipped: list[str] = []
    reason = "Оформление сервера в стиле Hunt: Showdown"
    default_role = guild.default_role

    category_specs = (
        ("camp", ("Текстовые каналы", "🏚️・ОХОТНИЧИЙ ЛАГЕРЬ"), "🏚️・ОХОТНИЧИЙ ЛАГЕРЬ"),
        ("voice", ("Основные каналы", "🔊・ОХОТНИЧЬИ УГОДЬЯ"), "🔊・ОХОТНИЧЬИ УГОДЬЯ"),
        ("admin", ("ADMIN", "🔒・АДМИНЫ"), "🔒・АДМИНЫ"),
        ("afk", ("AFK", "💤・AFK"), "💤・AFK"),
    )
    categories: dict[str, disnake.CategoryChannel] = {}
    source_camp = _find_category(guild, category_specs[0][1])
    for key, aliases, target_name in category_specs:
        category = _find_category(guild, aliases)
        if category is None and key in {"camp", "voice"}:
            category = await guild.create_category(target_name, reason=reason)
            changes.append(f"создана категория {target_name}")
        elif category is not None and category.name != target_name:
            await category.edit(name=target_name, reason=reason)
            changes.append(f"переименована категория в {target_name}")
        if category is not None:
            categories[key] = category
        elif key in {"admin", "afk"}:
            skipped.append(f"категория {aliases[0]} не найдена")

    info_category = _find_category(guild, ("📜・ВХОД В УГОДЬЯ",))
    if info_category is None:
        info_category = await guild.create_category(
            "📜・ВХОД В УГОДЬЯ",
            overwrites={
                default_role: disnake.PermissionOverwrite(
                    view_channel=True,
                    send_messages=False,
                    read_message_history=True,
                )
            },
            reason=reason,
        )
        changes.append("создана категория 📜・ВХОД В УГОДЬЯ")
    else:
        overwrite = info_category.overwrites_for(default_role)
        if (
            overwrite.view_channel is not True
            or overwrite.send_messages is not False
            or overwrite.read_message_history is not True
        ):
            await info_category.set_permissions(
                default_role,
                view_channel=True,
                send_messages=False,
                read_message_history=True,
                reason=reason,
            )
            changes.append("настроены права категории 📜・ВХОД В УГОДЬЯ")

    contracts_category = _find_category(guild, ("🎮・КОНТРАКТЫ",))
    if contracts_category is None:
        category_options = {"overwrites": source_camp.overwrites} if source_camp else {}
        contracts_category = await guild.create_category("🎮・КОНТРАКТЫ", reason=reason, **category_options)
        changes.append("создана категория 🎮・КОНТРАКТЫ")

    camp_category = categories.get("camp")
    voice_category = categories.get("voice")
    channel_specs = (
        (("rules", "📜・правила"), "📜・правила", info_category, "Правила охотничьих угодий."),
        (("верификация", "✅・верификация"), "✅・верификация", info_category, "Пройди проверку, чтобы получить доступ к угодьям."),
        (("приветственное-сообщение", "👋・приветствие"), "👋・приветствие", info_category, "Добро пожаловать в охотничий лагерь."),
        (("chat", "💬・общий-чат"), "💬・общий-чат", camp_category, "Общий костёр: общение, новости и истории охоты."),
        (("music", "🎵・музыка"), "🎵・музыка", camp_category, "Музыка: используй /music [запрос]."),
        (("dota_info", "🛡️・dota-2"), "🛡️・dota-2", contracts_category, "Матчи и новости Dota 2."),
        (("cs_info", "🔫・cs2"), "🔫・cs2", contracts_category, "Матчи и новости CS2 / Faceit."),
        (("Создать комнату", "➕・создать-комнату"), "➕・создать-комнату", voice_category, None),
    )
    channels: dict[str, disnake.abc.GuildChannel] = {}
    for aliases, target_name, category, topic in channel_specs:
        channel = _find_channel(guild, aliases)
        if channel is None:
            skipped.append(f"канал {aliases[0]} не найден")
            continue
        if category is None:
            skipped.append(f"категория для канала {target_name} не готова")
            continue
        if target_name in {"📜・правила", "✅・верификация", "👋・приветствие",
                           "💬・общий-чат", "🎵・музыка", "🛡️・dota-2", "🔫・cs2"}:
            if not isinstance(channel, disnake.TextChannel):
                skipped.append(f"{channel.name} не является текстовым каналом")
                continue
        elif not isinstance(channel, disnake.VoiceChannel):
            skipped.append(f"{channel.name} не является голосовым каналом")
            continue

        needs_edit = channel.name != target_name or channel.category_id != category.id
        if isinstance(channel, disnake.TextChannel) and topic is not None:
            needs_edit = needs_edit or channel.topic != topic
        if needs_edit and isinstance(channel, disnake.TextChannel):
            edited = await channel.edit(
                name=target_name,
                category=category,
                topic=topic,
                sync_permissions=False,
                reason=reason,
            )
            channel = edited or guild.get_channel(channel.id) or channel
            changes.append(f"обновлён канал {target_name}")
        elif needs_edit and isinstance(channel, disnake.VoiceChannel):
            edited = await channel.edit(
                name=target_name,
                category=category,
                sync_permissions=False,
                reason=reason,
            )
            channel = edited or guild.get_channel(channel.id) or channel
            changes.append(f"обновлён канал {target_name}")
        channels[target_name] = channel

    new_channels = (
        (info_category, "📯・объявления", "Объявления администрации. Канал только для чтения."),
        (contracts_category, "🔍・hunt-showdown", "Обсуждение Hunt: Showdown 1896, охотничьих сборок и событий."),
        (contracts_category, "🧭・поиск-отряда", "Ищи напарников: укажи игру, режим, время и голосовой канал."),
    )
    for category, name, topic in new_channels:
        channel = _find_channel(guild, (name,))
        if channel is None:
            channel = await guild.create_text_channel(
                name,
                category=category,
                topic=topic,
                overwrites=category.overwrites,
                reason=reason,
            )
            changes.append(f"создан канал {name}")
        elif isinstance(channel, disnake.TextChannel):
            if channel.category_id != category.id or channel.topic != topic:
                edited = await channel.edit(category=category, topic=topic, sync_permissions=False, reason=reason)
                channel = edited or guild.get_channel(channel.id) or channel
                changes.append(f"обновлён канал {name}")
        else:
            skipped.append(f"{name} уже существует, но это не текстовый канал")
            continue
        channels[name] = channel

    for target_name in ("📜・правила", "✅・верификация", "👋・приветствие", "📯・объявления"):
        channel = channels.get(target_name)
        if not isinstance(channel, disnake.TextChannel):
            continue
        everyone_overwrite = channel.overwrites_for(default_role)
        if (
            everyone_overwrite.view_channel is not True
            or everyone_overwrite.send_messages is not False
            or everyone_overwrite.read_message_history is not True
        ):
            everyone_overwrite.view_channel = True
            everyone_overwrite.send_messages = False
            everyone_overwrite.read_message_history = True
            await channel.set_permissions(default_role, overwrite=everyone_overwrite, reason=reason)
            changes.append(f"настроены права канала {target_name}")
        for role, overwrite in channel.overwrites.items():
            if isinstance(role, disnake.Role) and overwrite.send_messages is True:
                role_overwrite = channel.overwrites_for(role)
                role_overwrite.send_messages = False
                await channel.set_permissions(role, overwrite=role_overwrite, reason=reason)
                changes.append(f"ограничена отправка сообщений для роли {role.name} в {target_name}")

    bot_member = guild.me
    if bot_member is not None:
        rules_channel = channels.get("📜・правила")
        welcome_channel = channels.get("👋・приветствие")
        if isinstance(rules_channel, disnake.TextChannel):
            rules_embed = disnake.Embed(
                title="📜 Кодекс охотничьих угодий",
                description=(
                    "**1. Уважай охотников.** Запрещены травля, угрозы, дискриминация и провокации.\n"
                    "**2. Соблюдай порядок.** Без спама, флуда, рекламы и посторонних приглашений без согласия администрации.\n"
                    "**3. Играй честно.** Не продвигай читы, мошенничество и способы вредить другим игрокам.\n"
                    "**4. Береги личные данные.** Не публикуй чужую переписку и персональную информацию без разрешения.\n"
                    "**5. Выбирай правильный канал.** Не засоряй голосовые комнаты шумом и не мешай другим участникам.\n"
                    "**6. Контент — по правилам Discord.** Запрещены материалы 18+, шок-контент и незаконный контент.\n"
                    "**7. Следуй указаниям модерации.** За нарушения возможны предупреждение, мут или удаление с сервера."
                ),
                color=disnake.Color.from_rgb(139, 90, 43),
            )
            rules_embed.set_footer(text=HUNT_RULES_MARKER)
            await _upsert_setup_embed(rules_channel, rules_embed, HUNT_RULES_MARKER, bot_member.id)
            changes.append("опубликованы правила сервера")
        if isinstance(welcome_channel, disnake.TextChannel):
            verify_channel = channels.get("✅・верификация")
            verify_settings = get_verify_settings(guild.id)
            if verify_settings and isinstance(verify_channel, disnake.TextChannel):
                if verify_settings[0] == verify_channel.id:
                    verification_instruction = "2. Пройди ✅・верификация по кнопке, чтобы открыть доступ."
                else:
                    verification_instruction = (
                        f"2. Пройди верификацию в <#{verify_settings[0]}>, чтобы открыть доступ."
                    )
            else:
                verification_instruction = (
                    "2. Верификация ещё не настроена: попроси администратора выполнить `/set_verify`."
                )
            welcome_embed = disnake.Embed(
                title="🕯 Добро пожаловать в охотничий лагерь",
                description=(
                    "Здесь собираются охотники за новыми контрактами и хорошей компанией.\n\n"
                    "1. Прочитай 📜・правила.\n"
                    f"{verification_instruction}\n"
                    "3. Игровые роли можно получить у администрации; напарников ищи в 🧭・поиск-отряда.\n"
                    "4. Для голосовых комнат зайди в ➕・создать-комнату."
                ),
                color=disnake.Color.from_rgb(139, 90, 43),
            )
            welcome_embed.set_footer(text=HUNT_WELCOME_MARKER)
            await _upsert_setup_embed(welcome_channel, welcome_embed, HUNT_WELCOME_MARKER, bot_member.id)
            changes.append("опубликовано приветствие")

    return changes, skipped


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

        if not isinstance(channel, disnake.TextChannel) or not guest or not member_role:
            return await inter.response.send_message("❌ Канал или роль не найдены.", ephemeral=True)
        if guest.id == member_role.id:
            return await inter.response.send_message("❌ Роли гостя и участника должны различаться.", ephemeral=True)

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


class HuntSetupView(disnake.ui.View):
    def __init__(self, guild: disnake.Guild, owner_id: int):
        super().__init__(timeout=120)
        self.guild = guild
        self.owner_id = owner_id

    async def interaction_check(self, inter: disnake.MessageInteraction) -> bool:
        if inter.author.id != self.owner_id:
            await inter.response.send_message("Эту настройку может подтвердить только её инициатор.", ephemeral=True)
            return False
        return True

    @disnake.ui.button(label="Применить оформление", style=disnake.ButtonStyle.success, emoji="🕯")
    async def apply(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        await inter.response.defer(ephemeral=True)
        try:
            changes, skipped = await apply_hunt_server_setup(self.guild)
        except (disnake.Forbidden, disnake.HTTPException):
            log.exception("Не удалось полностью оформить сервер %s", self.guild.id)
            await inter.edit_original_message(
                content=(
                    "❌ Discord отклонил одно из изменений. Часть настройки могла уже примениться; "
                    "проверьте `/server_audit` и права бота перед повторным запуском."
                ),
                embed=None,
                view=None,
            )
            self.stop()
            return

        summary = f"✅ Оформление завершено. Выполнено действий: {len(changes)}."
        if skipped:
            summary += "\n\nНе изменено:\n" + "\n".join(f"• {item}" for item in skipped)
        if changes:
            summary += "\n\n" + "\n".join(f"• {item}" for item in changes)
        log.info(
            "Оформление сервера %s завершено: %s изменений, %s пропусков",
            self.guild.id,
            len(changes),
            len(skipped),
        )
        await inter.edit_original_message(content=summary, embed=None, view=None)
        self.stop()

    @disnake.ui.button(label="Отмена", style=disnake.ButtonStyle.secondary)
    async def cancel(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        await inter.response.edit_message(content="Настройка отменена; сервер не изменён.", embed=None, view=None)
        self.stop()


class AdminPanelCog(commands.Cog):
    def __init__(self, bot: commands.InteractionBot):
        self.bot = bot

    @commands.slash_command(
        description="Предпросмотр и оформление сервера в стиле Hunt: Showdown",
        default_member_permissions=disnake.Permissions(administrator=True)
    )
    async def hunt_setup(self, inter: disnake.ApplicationCommandInteraction):
        if inter.guild is None:
            return await inter.response.send_message("Эта команда доступна только на сервере.", ephemeral=True)
        bot_member = inter.guild.me
        if (
            bot_member is None
            or not bot_member.guild_permissions.manage_channels
            or not bot_member.guild_permissions.manage_roles
        ):
            return await inter.response.send_message(
                "Боту нужны разрешения «Управление каналами» и «Управление ролями» "
                "для создания каналов и настройки прав на них.",
                ephemeral=True,
            )

        embed = disnake.Embed(
            title="🕯 Оформление охотничьего лагеря",
            description=(
                "После подтверждения бот сгруппирует и переименует известные каналы, "
                "создаст категории и игровые каналы Hunt: Showdown, настроит правила и приветствие.\n\n"
                "Каналы и роли не удаляются. Права ролей `Bots` и `Админ` не меняются; "
                "для правил, верификации и приветствия будет включён режим чтения для участников."
            ),
            color=disnake.Color.from_rgb(139, 90, 43),
        )
        embed.add_field(
            name="Будет добавлено",
            value="Категории входа и игровых контрактов; каналы объявлений, Hunt: Showdown и поиска отряда.",
            inline=False,
        )
        embed.add_field(
            name="Новая структура",
            value=(
                "📜・ВХОД В УГОДЬЯ — правила, верификация, приветствие, объявления\n"
                "🏚️・ОХОТНИЧИЙ ЛАГЕРЬ — общий чат и музыка\n"
                "🎮・КОНТРАКТЫ — Hunt: Showdown, Dota 2, CS2 и поиск отряда\n"
                "🔊・ОХОТНИЧЬИ УГОДЬЯ — создание голосовых комнат\n"
                "Админская и AFK-категории сохраняются."
            ),
            inline=False,
        )
        await inter.response.send_message(
            embed=embed,
            view=HuntSetupView(inter.guild, inter.author.id),
            ephemeral=True,
        )

    @commands.slash_command(
        description="Выгрузить роли, каналы и права сервера для аудита",
        default_member_permissions=disnake.Permissions(administrator=True)
    )
    async def server_audit(self, inter: disnake.ApplicationCommandInteraction):
        audit = build_server_audit(inter.guild)
        payload = json.dumps(audit, ensure_ascii=False, indent=2).encode("utf-8")
        filename = f"server-audit-{inter.guild.id}.json"
        file = disnake.File(io.BytesIO(payload), filename=filename)
        await inter.response.send_message(
            "Аудит сервера готов. Файл содержит список каналов, ролей и настроенных прав; ничего на сервере не изменено.",
            file=file,
            ephemeral=True,
        )

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
