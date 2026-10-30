"""Approved TSH channel changes; never modifies role permissions."""
import asyncio
import logging
import disnake
from db.database import conn, set_channel

log = logging.getLogger(__name__)
GUILD_ID = 133839826364530688
MEMBER_ROLES = (1406348291888775208, 1482510228548358144, 1482510497994637352, 1488204338873765998, 1487051931107983522)


async def readonly(channel, guild, public=False):
    # Preserve unrelated permissions; remove explicit send grants at every overwrite.
    overwrites = channel.overwrites
    for target, ow in overwrites.items():
        ow.send_messages = False
        ow.send_messages_in_threads = False
        ow.create_public_threads = False
        ow.create_private_threads = False
    everyone = overwrites.get(guild.default_role, disnake.PermissionOverwrite())
    everyone.update(send_messages=False, send_messages_in_threads=False, create_public_threads=False, create_private_threads=False)
    if public:
        everyone.update(view_channel=True, read_message_history=True)
    overwrites[guild.default_role] = everyone
    for rid in MEMBER_ROLES:
        role = guild.get_role(rid)
        if role:
            ow = overwrites.get(role, disnake.PermissionOverwrite())
            ow.update(view_channel=True, read_message_history=True, send_messages=False, send_messages_in_threads=False, use_application_commands=True)
            overwrites[role] = ow
    ow = overwrites.get(guild.me, disnake.PermissionOverwrite())
    ow.update(view_channel=True, read_message_history=True, send_messages=True, embed_links=True, attach_files=True)
    overwrites[guild.me] = ow
    await channel.edit(overwrites=overwrites, reason="Согласованная настройка каналов TSH")


async def ensure_panel(guild, channel_id, kind, title, description):
    channel = guild.get_channel(channel_id)
    if not isinstance(channel, disnake.TextChannel):
        return
    conn.execute("CREATE TABLE IF NOT EXISTS service_panels (guild_id INTEGER, kind TEXT, message_id INTEGER, PRIMARY KEY(guild_id, kind))")
    conn.commit()
    row = conn.execute("SELECT message_id FROM service_panels WHERE guild_id=? AND kind=?", (guild.id, kind)).fetchone()
    message = None
    if row:
        try:
            message = await channel.fetch_message(row[0])
        except disnake.NotFound:
            pass
    embed = disnake.Embed(title=title, description=description, color=disnake.Color.blurple())
    if message:
        await message.edit(embed=embed)
    else:
        message = await channel.send(embed=embed)
        conn.execute("INSERT OR REPLACE INTO service_panels VALUES (?, ?, ?)", (guild.id, kind, message.id))
        conn.commit()
    if not message.pinned:
        await message.pin(reason="Постоянная инструкция по привязке аккаунта")


_setup_lock = asyncio.Lock()


async def apply_setup(guild):
    async with _setup_lock:
        await _apply_setup(guild)


async def _apply_setup(guild):
    if guild.id != GUILD_ID:
        return
    key = "approved_channel_setup_v1"
    row = conn.execute("SELECT 1 FROM completed_migrations WHERE migration_key=? AND scope_id=?", (key, guild.id)).fetchone()
    if not row:
        admin = guild.get_channel(381701738018635776)
        welcome = guild.get_channel(1486701963465986148)
        if not admin or not admin.category or not isinstance(welcome, disnake.TextChannel):
            raise RuntimeError("Не найдены обязательные каналы: изменения не завершены")
        await admin.edit(sync_permissions=True, reason="Синхронизация закрытой Админки")
        await readonly(welcome, guild, public=True)
        set_channel(guild.id, welcome.id)
        for cid in (1487101267624067233, 1487112222571499632):
            channel = guild.get_channel(cid)
            if channel:
                await readonly(channel, guild)
        music = guild.get_channel(705447847473971200)
        if music:
            for rid in MEMBER_ROLES:
                role = guild.get_role(rid)
                if role:
                    ow = music.overwrites_for(role)
                    ow.update(view_channel=True, read_message_history=True, use_application_commands=True)
                    await music.set_permissions(role, overwrite=ow)
        old = guild.get_channel(705436573671358524)
        if old:
            await old.delete(reason="Удаление старого people по запросу владельца; приветствия перенесены")
        conn.execute("INSERT OR IGNORE INTO completed_migrations VALUES (?, ?, datetime('now'))", (key, guild.id))
        conn.commit()
        log.info("Согласованные настройки каналов TSH применены")
    await ensure_panel(guild, 1487101267624067233, "dota", "🛡️ Dota 2 — твой профиль",
                       "Привяжи Steam командой `/link_steam`: укажи ссылку на профиль или Steam ID.\nСтатистика: `/dota` · Матчи: `/dota_matches` · Итоги недели: `/dota_week`.\nВ этом канале публикует бот. Команды доступны участникам; пароли не нужны.")
    await ensure_panel(guild, 1487112222571499632, "cs2", "🔫 CS2 / FACEIT — твой профиль",
                       "Привяжи аккаунт командой `/link_cs`: укажи ник FACEIT.\nСтатистика: `/cs`.\nВ этом канале публикует бот. Команды доступны участникам; пароли не нужны.")
