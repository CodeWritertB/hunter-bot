import time
import disnake
import logging
from disnake.ext import commands
from db.database import (
    get_lobby, set_lobby,
    add_temp_channel, get_temp_channel,
    update_temp_channel_owner, remove_temp_channel, is_temp_channel,
    get_music_channel, save_room_settings, get_room_settings
)

log = logging.getLogger("cogs.voice_rooms")

panel_messages: dict[int, disnake.Message] = {}
raise_cooldowns: dict[int, float] = {}  # channel_id -> timestamp
# channel_id -> {user_id -> left_timestamp}
recent_members: dict[int, dict[int, float]] = {}

PRESET_NAMES = [
    "Hunt: Showdown 1896",
    "Dota 2",
    "Counter-Strike 2",
    "Minecraft",
    "Battlefield",
    "Elden Ring",
]


def save_vc_settings(vc: disnake.VoiceChannel, owner_id: int):
    ow = vc.overwrites_for(vc.guild.default_role)
    save_room_settings(
        owner_id=owner_id,
        name=vc.name,
        user_limit=vc.user_limit,
        locked=ow.connect is False,
        hidden=ow.view_channel is False
    )


def build_panel_embed(vc: disnake.VoiceChannel, owner: disnake.Member) -> disnake.Embed:
    ow = vc.overwrites_for(vc.guild.default_role)
    hidden = ow.view_channel is False
    embed = disnake.Embed(
        title="・Управление комнатой",
        color=disnake.Color.blurple()
    )
    embed.add_field(name="Владелец", value=owner.mention, inline=True)
    embed.add_field(name="Видимость", value="🙈 Скрыта" if hidden else "👁 Видна", inline=True)
    embed.add_field(name="Лимит", value=str(vc.user_limit) if vc.user_limit else "∞", inline=True)
    embed.set_footer(text="Кнопки работают только для владельца комнаты")
    return embed


# --- Название ---

class NameSelectView(disnake.ui.View):
    def __init__(self, vc: disnake.VoiceChannel, owner: disnake.Member):
        super().__init__(timeout=60)
        self.vc = vc
        self.owner = owner
        options = [disnake.SelectOption(label=name, value=name) for name in PRESET_NAMES]
        self.add_item(NameSelect(vc, owner, options))
        self.add_item(CustomNameButton(vc, owner))


class NameSelect(disnake.ui.StringSelect):
    def __init__(self, vc: disnake.VoiceChannel, owner: disnake.Member, options):
        super().__init__(placeholder="Выберете название", options=options)
        self.vc = vc
        self.owner = owner

    async def callback(self, inter: disnake.MessageInteraction):
        name = self.values[0]
        await self.vc.edit(name=name)
        save_vc_settings(self.vc, self.owner.id)
        log.info(f"[{inter.guild.name}] Комната переименована в '{name}' ({inter.author})")
        if msg := panel_messages.get(self.vc.id):
            await msg.edit(embed=build_panel_embed(self.vc, self.owner))
        await inter.response.edit_message(content=f"✅ Название: **{name}**", view=None, embed=None)


class CustomNameButton(disnake.ui.Button):
    def __init__(self, vc: disnake.VoiceChannel, owner: disnake.Member):
        super().__init__(label="Установить собственное название", style=disnake.ButtonStyle.secondary)
        self.vc = vc
        self.owner = owner

    async def callback(self, inter: disnake.MessageInteraction):
        await inter.response.send_modal(RenameModal(self.vc, self.owner))


class RenameModal(disnake.ui.Modal):
    def __init__(self, vc: disnake.VoiceChannel, owner: disnake.Member):
        self.vc = vc
        self.owner = owner
        super().__init__(title="・Изменение названия комнаты", components=[
            disnake.ui.TextInput(label="Название", custom_id="name", max_length=100)
        ])

    async def callback(self, inter: disnake.ModalInteraction):
        name = inter.text_values["name"]
        await self.vc.edit(name=name)
        save_vc_settings(self.vc, self.owner.id)
        log.info(f"[{inter.guild.name}] Комната переименована в '{name}' ({inter.author})")
        if msg := panel_messages.get(self.vc.id):
            await msg.edit(embed=build_panel_embed(self.vc, self.owner))
        await inter.response.send_message(f"✅ Название: **{name}**", ephemeral=True)


# --- Лимит ---

class LimitModal(disnake.ui.Modal):
    def __init__(self, vc: disnake.VoiceChannel, owner: disnake.Member):
        self.vc = vc
        self.owner = owner
        super().__init__(title="・Лимит участников", components=[
            disnake.ui.TextInput(label="Укажите новый лимит человек комнаты", custom_id="limit", max_length=3, placeholder="0 = без лимита")
        ])

    async def callback(self, inter: disnake.ModalInteraction):
        try:
            count = int(inter.text_values["limit"])
        except ValueError:
            return await inter.response.send_message("❌ Введи число.", ephemeral=True)
        await self.vc.edit(user_limit=count)
        save_vc_settings(self.vc, self.owner.id)
        log.info(f"[{inter.guild.name}] Лимит '{self.vc.name}' → {count} ({inter.author})")
        if msg := panel_messages.get(self.vc.id):
            await msg.edit(embed=build_panel_embed(self.vc, self.owner))
        await inter.response.send_message(f"✅ Лимит: {count if count else '∞'}", ephemeral=True)


# --- Видимость ---

class VisibilityView(disnake.ui.View):
    def __init__(self, vc: disnake.VoiceChannel, owner: disnake.Member):
        super().__init__(timeout=60)
        self.vc = vc
        self.owner = owner
        ow = vc.overwrites_for(vc.guild.default_role)
        hidden = ow.view_channel is False
        btn = disnake.ui.Button(
            label="Проявить" if hidden else "Скрыть",
            style=disnake.ButtonStyle.success if hidden else disnake.ButtonStyle.secondary,
            emoji="👁" if hidden else "🙈"
        )
        btn.callback = self.toggle
        self.add_item(btn)

    async def toggle(self, inter: disnake.MessageInteraction):
        ow = self.vc.overwrites_for(inter.guild.default_role)
        hidden = ow.view_channel is False
        await self.vc.set_permissions(inter.guild.default_role, view_channel=hidden)
        save_vc_settings(self.vc, self.owner.id)
        log.info(f"[{inter.guild.name}] '{self.vc.name}' {'показана' if hidden else 'скрыта'} ({inter.author})")
        if msg := panel_messages.get(self.vc.id):
            await msg.edit(embed=build_panel_embed(self.vc, self.owner))
        status = "👁 Проявлена" if hidden else "🙈 Скрыта"
        await inter.response.edit_message(content=f"✅ {status}", view=None, embed=None)


# --- Передача владельца ---

class TransferView(disnake.ui.View):
    def __init__(self, vc: disnake.VoiceChannel, owner: disnake.Member):
        super().__init__(timeout=30)
        self.vc = vc
        self.owner = owner
        members = [m for m in vc.members if m.id != owner.id]
        if members:
            options = [disnake.SelectOption(label=m.display_name, value=str(m.id)) for m in members[:25]]
            self.add_item(TransferSelect(vc, owner, options))


class TransferSelect(disnake.ui.StringSelect):
    def __init__(self, vc, owner, options):
        super().__init__(placeholder="Выберете владельца", options=options)
        self.vc = vc
        self.owner = owner

    async def callback(self, inter: disnake.MessageInteraction):
        new_owner = inter.guild.get_member(int(self.values[0]))
        if not new_owner:
            return await inter.response.send_message("❌ Участник не найден.", ephemeral=True)
        update_temp_channel_owner(self.vc.id, new_owner.id)
        await self.vc.set_permissions(new_owner, manage_channels=True, move_members=True, mute_members=True)
        await self.vc.set_permissions(self.owner, overwrite=None)
        log.info(f"[{inter.guild.name}] Владелец '{self.vc.name}': {self.owner} → {new_owner}")
        if msg := panel_messages.get(self.vc.id):
            await msg.edit(embed=build_panel_embed(self.vc, new_owner), view=RoomPanel(self.vc, new_owner))
        await inter.response.edit_message(content=f"✅ Владелец передан: {new_owner.mention}", view=None, embed=None)


# --- Баны (выбор участника) ---

class MemberActionView(disnake.ui.View):
    def __init__(self, vc: disnake.VoiceChannel, owner: disnake.Member, action: str):
        super().__init__(timeout=30)
        self.vc = vc
        self.owner = owner
        self.action = action

        now = time.time()
        # Текущие участники канала
        current_ids = {m.id for m in vc.members if m.id != owner.id}
        # Недавно покинувшие (последние 2 минуты)
        recent = recent_members.get(vc.id, {})
        recent_ids = {uid for uid, left_at in recent.items() if now - left_at <= 120}

        all_ids = current_ids | recent_ids
        members = [vc.guild.get_member(uid) for uid in all_ids if vc.guild.get_member(uid)]

        if members:
            options = []
            for m in members[:25]:
                label = m.display_name
                if m.id not in current_ids:
                    label += " (недавно был)"
                options.append(disnake.SelectOption(label=label, value=str(m.id)))
            self.add_item(MemberActionSelect(vc, owner, action, options))


class MemberActionSelect(disnake.ui.StringSelect):
    def __init__(self, vc, owner, action, options):
        super().__init__(placeholder="Выберете участника", options=options)
        self.vc = vc
        self.owner = owner
        self.action = action

    async def callback(self, inter: disnake.MessageInteraction):
        target = inter.guild.get_member(int(self.values[0]))
        if not target:
            return await inter.response.send_message("❌ Участник не найден.", ephemeral=True)

        if self.action == "ban":
            if target.voice and target.voice.channel == self.vc:
                await target.move_to(None)
            await self.vc.set_permissions(target, connect=False, view_channel=False)
            log.info(f"[{inter.guild.name}] {target} забанен в '{self.vc.name}' ({self.owner})")
            await inter.response.edit_message(content=f"🚫 {target.mention} забанен в комнате.", view=None, embed=None)


# --- Главная панель ---

class RoomPanel(disnake.ui.View):
    def __init__(self, vc: disnake.VoiceChannel, owner: disnake.Member):
        super().__init__(timeout=None)
        self.vc = vc
        self.owner = owner

    def check(self, inter: disnake.MessageInteraction) -> bool:
        return inter.author.id == self.owner.id

    # Ряд 1
    @disnake.ui.button(label="Название", emoji="✏️", style=disnake.ButtonStyle.secondary, row=0)
    async def name_btn(self, button, inter: disnake.MessageInteraction):
        if not self.check(inter):
            return await inter.response.send_message("❌ Только владелец.", ephemeral=True)
        embed = disnake.Embed(
            title="・Изменение названия комнаты",
            description="Вы можете поставить собственное название канала, либо выбрать готовое из списка",
            color=disnake.Color.blurple()
        )
        await inter.response.send_message(embed=embed, view=NameSelectView(self.vc, self.owner), ephemeral=True)

    @disnake.ui.button(label="Лимит", emoji="👥", style=disnake.ButtonStyle.secondary, row=0)
    async def limit_btn(self, button, inter: disnake.MessageInteraction):
        if not self.check(inter):
            return await inter.response.send_message("❌ Только владелец.", ephemeral=True)
        await inter.response.send_modal(LimitModal(self.vc, self.owner))

    @disnake.ui.button(label="Видимость", emoji="👁", style=disnake.ButtonStyle.secondary, row=0)
    async def visibility_btn(self, button, inter: disnake.MessageInteraction):
        if not self.check(inter):
            return await inter.response.send_message("❌ Только владелец.", ephemeral=True)
        ow = self.vc.overwrites_for(inter.guild.default_role)
        hidden = ow.view_channel is False
        embed = disnake.Embed(
            title="・Видимость комнаты",
            description="Скрыть/Проявить по кнопке ниже",
            color=disnake.Color.blurple()
        )
        embed.add_field(name="Текущий статус", value="🙈 Скрыта" if hidden else "👁 Видна")
        await inter.response.send_message(embed=embed, view=VisibilityView(self.vc, self.owner), ephemeral=True)

    @disnake.ui.button(label="Владелец", emoji="👑", style=disnake.ButtonStyle.primary, row=0)
    async def owner_btn(self, button, inter: disnake.MessageInteraction):
        if not self.check(inter):
            return await inter.response.send_message("❌ Только владелец.", ephemeral=True)
        members = [m for m in self.vc.members if m.id != self.owner.id]
        if not members:
            return await inter.response.send_message("❌ В канале нет других участников.", ephemeral=True)
        embed = disnake.Embed(
            title="・Передача прав владельца комнаты",
            description="Выберете владельца",
            color=disnake.Color.blurple()
        )
        await inter.response.send_message(embed=embed, view=TransferView(self.vc, self.owner), ephemeral=True)

    # Ряд 2
    @disnake.ui.button(label="Видео", emoji="📹", style=disnake.ButtonStyle.secondary, row=1)
    async def video_btn(self, button, inter: disnake.MessageInteraction):
        if not self.check(inter):
            return await inter.response.send_message("❌ Только владелец.", ephemeral=True)
        ow = self.vc.overwrites_for(inter.guild.default_role)
        currently_denied = ow.stream == False
        await self.vc.set_permissions(inter.guild.default_role, stream=currently_denied or None)
        status = "✅ Видео разрешено" if currently_denied else "🚫 Видео запрещено"
        log.info(f"[{inter.guild.name}] '{self.vc.name}' видео: {status} ({inter.author})")
        if msg := panel_messages.get(self.vc.id):
            await msg.edit(embed=build_panel_embed(self.vc, self.owner))
        await inter.response.send_message(status, ephemeral=True)

    @disnake.ui.button(label="Voice", emoji="🎤", style=disnake.ButtonStyle.secondary, row=1)
    async def voice_btn(self, button, inter: disnake.MessageInteraction):
        if not self.check(inter):
            return await inter.response.send_message("❌ Только владелец.", ephemeral=True)
        ow = self.vc.overwrites_for(inter.guild.default_role)
        currently_denied = ow.speak == False
        await self.vc.set_permissions(inter.guild.default_role, speak=currently_denied or None)
        status = "✅ Голос разрешён" if currently_denied else "🚫 Голос запрещён"
        log.info(f"[{inter.guild.name}] '{self.vc.name}' голос: {status} ({inter.author})")
        if msg := panel_messages.get(self.vc.id):
            await msg.edit(embed=build_panel_embed(self.vc, self.owner))
        await inter.response.send_message(status, ephemeral=True)

    @disnake.ui.button(label="Баны", emoji="🔨", style=disnake.ButtonStyle.danger, row=1)
    async def ban_btn(self, button, inter: disnake.MessageInteraction):
        if not self.check(inter):
            return await inter.response.send_message("❌ Только владелец.", ephemeral=True)
        await inter.response.send_message("🔨 Выбери кого забанить:", view=MemberActionView(self.vc, self.owner, "ban"), ephemeral=True)

    @disnake.ui.button(label="Поднять", emoji="⬆️", style=disnake.ButtonStyle.success, row=1)
    async def raise_btn(self, button, inter: disnake.MessageInteraction):
        if not self.check(inter):
            return await inter.response.send_message("❌ Только владелец.", ephemeral=True)
        now = time.time()
        last = raise_cooldowns.get(self.vc.id, 0)
        remaining = 180 - (now - last)
        if remaining > 0:
            return await inter.response.send_message(
                f"⏳ Поднять можно через **{int(remaining)}** сек.", ephemeral=True
            )
        raise_cooldowns[self.vc.id] = now
        if msg := panel_messages.get(self.vc.id):
            try:
                await msg.delete()
            except Exception:
                pass
        new_msg = await self.vc.send(embed=build_panel_embed(self.vc, self.owner), view=RoomPanel(self.vc, self.owner))
        panel_messages[self.vc.id] = new_msg
        await inter.response.send_message("✅ Комната поднята.", ephemeral=True)


# --- Cog ---

class VoiceRooms(commands.Cog):
    def __init__(self, bot: commands.InteractionBot):
        self.bot = bot

    @commands.slash_command(
        description="Назначить лобби-канал для создания приватных комнат",
        default_member_permissions=disnake.Permissions(administrator=True)
    )
    async def set_new_channel(self, inter: disnake.ApplicationCommandInteraction, channel: disnake.VoiceChannel):
        set_lobby(inter.guild.id, channel.id)
        log.info(f"[{inter.guild.name}] Лобби установлено: #{channel.name} ({inter.author})")
        await inter.response.send_message(f"✅ Лобби-канал установлен: {channel.mention}", ephemeral=True)

    @commands.slash_command(
        description="Забрать управление голосовой комнатой",
        default_member_permissions=disnake.Permissions(administrator=True)
    )
    async def room_takeover(self, inter: disnake.ApplicationCommandInteraction, channel: disnake.VoiceChannel):
        # Проверяем что канал является временной комнатой
        if not is_temp_channel(channel.id):
            return await inter.response.send_message("❌ Это не приватная комната.", ephemeral=True)

        row = get_temp_channel(channel.id)
        old_owner = inter.guild.get_member(row[1]) if row else None

        # Передаём права новому владельцу
        update_temp_channel_owner(channel.id, inter.author.id)
        await channel.set_permissions(inter.author, manage_channels=True, move_members=True, mute_members=True)
        if old_owner:
            await channel.set_permissions(old_owner, overwrite=None)

        # Обновляем панель
        if msg := panel_messages.get(channel.id):
            await msg.edit(embed=build_panel_embed(channel, inter.author), view=RoomPanel(channel, inter.author))

        log.info(f"[{inter.guild.name}] Админ {inter.author} забрал управление комнатой '{channel.name}' у {old_owner}")
        await inter.response.send_message(f"✅ Управление комнатой **{channel.name}** передано вам.", ephemeral=True)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: disnake.Member, before: disnake.VoiceState, after: disnake.VoiceState):
        # Игнорируем бота
        if member.bot:
            return
        guild = member.guild
        lobby_id = get_lobby(guild.id)

        if after.channel and after.channel.id == lobby_id:
            category = after.channel.category
            settings = get_room_settings(member.id)
            name = settings[0] if settings else f"🔊 {member.display_name}"
            user_limit = settings[1] if settings else 0

            vc = await guild.create_voice_channel(
                name=name,
                category=category,
                user_limit=user_limit,
                overwrites={
                    guild.default_role: disnake.PermissionOverwrite(view_channel=True, connect=True),
                    member: disnake.PermissionOverwrite(manage_channels=True, move_members=True, mute_members=True),
                }
            )
            if settings:
                locked, hidden = bool(settings[2]), bool(settings[3])
                if locked or hidden:
                    await vc.set_permissions(guild.default_role, connect=not locked, view_channel=not hidden)

            await member.move_to(vc)
            add_temp_channel(vc.id, guild.id, member.id)
            msg = await vc.send(embed=build_panel_embed(vc, member), view=RoomPanel(vc, member))
            panel_messages[vc.id] = msg
            log.info(f"[{guild.name}] Создана комната: {vc.name} для {member}")

        if before.channel and is_temp_channel(before.channel.id):
            vc = before.channel
            # Запоминаем когда участник покинул комнату, чистим старые записи
            if vc.id not in recent_members:
                recent_members[vc.id] = {}
            now_t = time.time()
            recent_members[vc.id][member.id] = now_t
            # Чистим записи старше 2 минут
            recent_members[vc.id] = {uid: t for uid, t in recent_members[vc.id].items() if now_t - t <= 120}

            if len(vc.members) == 0:
                panel_messages.pop(vc.id, None)
                recent_members.pop(vc.id, None)
                raise_cooldowns.pop(vc.id, None)
                remove_temp_channel(vc.id)
                await vc.delete()
                log.info(f"[{guild.name}] Удалена пустая комната: {vc.name}")
            else:
                row = get_temp_channel(vc.id)
                if row and row[1] == member.id:
                    new_owner = vc.members[0]
                    update_temp_channel_owner(vc.id, new_owner.id)
                    await vc.set_permissions(new_owner, manage_channels=True, move_members=True, mute_members=True)
                    await vc.set_permissions(member, overwrite=None)
                    if msg := panel_messages.get(vc.id):
                        await msg.edit(embed=build_panel_embed(vc, new_owner), view=RoomPanel(vc, new_owner))
                    log.info(f"[{guild.name}] Владелец '{vc.name}' передан: {new_owner}")


def setup(bot: commands.InteractionBot):
    bot.add_cog(VoiceRooms(bot))
