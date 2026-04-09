import asyncio
import logging
import os
import socket
import struct
import a2s
import disnake
from disnake.ext import commands
from db.database import _get_server, _upsert_server

log = logging.getLogger("cogs.gameserver")

CS_SERVER_HOST = "185.248.101.137"
CS_SERVER_PORT = 30200
CS_SERVER_ADDR = (CS_SERVER_HOST, CS_SERVER_PORT)
CS_RCON_PASSWORD = os.getenv("CS_RCON_PASSWORD", "")

CHECK_INTERVAL = 30

prev_players: dict[int, set[str]] = {}

CS2_MAPS = [
    "de_mirage", "de_dust2", "de_inferno", "de_nuke",
    "de_overpass", "de_ancient", "de_anubis", "de_vertigo"
]


# --- RCON ---

async def rcon_command(command: str) -> str:
    """Отправляет RCON команду на сервер CS2."""
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, _rcon_sync, command)
        return result
    except Exception as e:
        return f"Ошибка: {e}"


def _rcon_sync(command: str) -> str:
    """Синхронный RCON клиент (Source RCON Protocol)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(5)
        s.connect((CS_SERVER_HOST, CS_SERVER_PORT))

        def send_packet(pkt_id: int, pkt_type: int, body: str):
            body_enc = body.encode("utf-8") + b"\x00\x00"
            size = 4 + 4 + len(body_enc)
            packet = struct.pack("<iii", size, pkt_id, pkt_type) + body_enc
            s.sendall(packet)

        def recv_packet():
            raw_size = s.recv(4)
            if len(raw_size) < 4:
                return None, None, ""
            size = struct.unpack("<i", raw_size)[0]
            data = b""
            while len(data) < size:
                chunk = s.recv(size - len(data))
                if not chunk:
                    break
                data += chunk
            pkt_id = struct.unpack("<i", data[0:4])[0]
            pkt_type = struct.unpack("<i", data[4:8])[0]
            body = data[8:-2].decode("utf-8", errors="replace")
            return pkt_id, pkt_type, body

        # Авторизация
        send_packet(1, 3, CS_RCON_PASSWORD)
        pkt_id, pkt_type, _ = recv_packet()
        if pkt_id == -1:
            return "❌ Неверный RCON пароль"

        # Команда
        send_packet(2, 2, command)
        _, _, response = recv_packet()
        return response.strip() or "✅ Команда выполнена"


# --- Steam Query ---

async def get_server_info():
    loop = asyncio.get_event_loop()
    try:
        info = await loop.run_in_executor(None, lambda: a2s.info(CS_SERVER_ADDR, timeout=3))
        players = await loop.run_in_executor(None, lambda: a2s.players(CS_SERVER_ADDR, timeout=3))
        return info, players
    except Exception as e:
        log.warning(f"Ошибка запроса к CS серверу: {e}")
        return None, []


def build_server_embed(info: a2s.SourceInfo, players: list) -> disnake.Embed:
    embed = disnake.Embed(
        title=f"🎮 {info.server_name}",
        color=disnake.Color.green() if info.player_count > 0 else disnake.Color.greyple()
    )
    embed.add_field(name="Карта", value=info.map_name, inline=True)
    embed.add_field(name="Игроки", value=f"{info.player_count}/{info.max_players}", inline=True)
    embed.add_field(name="Пинг", value=f"{info.ping * 1000:.0f}ms", inline=True)
    if players:
        sorted_p = sorted(players, key=lambda p: p.score, reverse=True)
        lines = [f"`{p.score:>3}` {p.name}" for p in sorted_p[:20]]
        embed.add_field(name="Игроки", value="\n".join(lines), inline=False)
    embed.set_footer(text=f"{CS_SERVER_HOST}:{CS_SERVER_PORT}")
    return embed


# --- Модальные окна ---

class KickModal(disnake.ui.Modal):
    def __init__(self):
        super().__init__(title="Кикнуть игрока", components=[
            disnake.ui.TextInput(label="Ник игрока", custom_id="name", placeholder="Точный ник")
        ])

    async def callback(self, inter: disnake.ModalInteraction):
        name = inter.text_values["name"]
        result = await rcon_command(f'kickid "{name}"')
        await inter.response.send_message(f"🦵 `{name}`: {result}", ephemeral=True)


class BanModal(disnake.ui.Modal):
    def __init__(self):
        super().__init__(title="Забанить игрока", components=[
            disnake.ui.TextInput(label="Ник игрока", custom_id="name", placeholder="Точный ник"),
            disnake.ui.TextInput(label="Время (минуты, 0 = навсегда)", custom_id="time", placeholder="60", max_length=6)
        ])

    async def callback(self, inter: disnake.ModalInteraction):
        name = inter.text_values["name"]
        time = inter.text_values["time"] or "0"
        result = await rcon_command(f'banid {time} "{name}"')
        await inter.response.send_message(f"🔨 `{name}` на {time} мин: {result}", ephemeral=True)


class SayModal(disnake.ui.Modal):
    def __init__(self):
        super().__init__(title="Сообщение в чат сервера", components=[
            disnake.ui.TextInput(label="Текст", custom_id="text", max_length=200)
        ])

    async def callback(self, inter: disnake.ModalInteraction):
        text = inter.text_values["text"]
        result = await rcon_command(f'say "[Discord] {text}"')
        await inter.response.send_message(f"💬 Отправлено: {result}", ephemeral=True)


class MapSelectView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=30)
        options = [disnake.SelectOption(label=m, value=m) for m in CS2_MAPS]
        self.add_item(MapSelect(options))


class MapSelect(disnake.ui.StringSelect):
    def __init__(self, options):
        super().__init__(placeholder="Выбери карту...", options=options)

    async def callback(self, inter: disnake.MessageInteraction):
        map_name = self.values[0]
        result = await rcon_command(f"changelevel {map_name}")
        await inter.response.edit_message(content=f"🗺 Смена карты на **{map_name}**: {result}", view=None, embed=None)


# --- Главная панель ---

class AdminPanel(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @disnake.ui.button(label="📊 Статус", style=disnake.ButtonStyle.secondary, row=0)
    async def status_btn(self, button, inter: disnake.MessageInteraction):
        await inter.response.defer(ephemeral=True)
        info, players = await get_server_info()
        if not info:
            return await inter.edit_original_response(content="❌ Сервер недоступен.")
        await inter.edit_original_response(embed=build_server_embed(info, players))

    @disnake.ui.button(label="🗺 Сменить карту", style=disnake.ButtonStyle.primary, row=0)
    async def map_btn(self, button, inter: disnake.MessageInteraction):
        await inter.response.send_message("Выбери карту:", view=MapSelectView(), ephemeral=True)

    @disnake.ui.button(label="🦵 Кикнуть", style=disnake.ButtonStyle.danger, row=1)
    async def kick_btn(self, button, inter: disnake.MessageInteraction):
        await inter.response.send_modal(KickModal())

    @disnake.ui.button(label="🔨 Забанить", style=disnake.ButtonStyle.danger, row=1)
    async def ban_btn(self, button, inter: disnake.MessageInteraction):
        await inter.response.send_modal(BanModal())

    @disnake.ui.button(label="💬 Написать в чат", style=disnake.ButtonStyle.secondary, row=1)
    async def say_btn(self, button, inter: disnake.MessageInteraction):
        await inter.response.send_modal(SayModal())


class GameServer(commands.Cog):
    def __init__(self, bot: commands.InteractionBot):
        self.bot = bot
        self.monitor_task = bot.loop.create_task(self.monitor_loop())

    def cog_unload(self):
        self.monitor_task.cancel()

    async def monitor_loop(self):
        """Отслеживает вход/выход игроков на сервере."""
        await self.bot.wait_until_ready()
        # Инициализируем prev_players текущим состоянием чтобы не спамить при старте
        info, players = await get_server_info()
        if info:
            current = {p.name for p in players}
            for guild in self.bot.guilds:
                prev_players[guild.id] = current
        while not self.bot.is_closed():
            try:
                info, players = await get_server_info()
                if info is None:
                    await asyncio.sleep(CHECK_INTERVAL)
                    continue

                current_names = {p.name for p in players}

                for guild in self.bot.guilds:
                    channel_id = _get_server(guild.id, "cs_server_channel_id")
                    if not channel_id:
                        continue
                    channel = self.bot.get_channel(channel_id)
                    if not channel:
                        continue

                    prev = prev_players.get(guild.id, set())
                    joined = current_names - prev
                    left = prev - current_names

                    for name in joined:
                        embed = disnake.Embed(
                            description=f"🟢 **{name}** зашёл на сервер • {info.player_count}/{info.max_players} • {info.map_name}",
                            color=disnake.Color.green()
                        )
                        await channel.send(embed=embed)

                    for name in left:
                        embed = disnake.Embed(
                            description=f"🔴 **{name}** вышел с сервера • {info.player_count}/{info.max_players} • {info.map_name}",
                            color=disnake.Color.red()
                        )
                        await channel.send(embed=embed)

                    prev_players[guild.id] = current_names

            except Exception as e:
                log.error(f"Ошибка в monitor_loop: {e}")

            await asyncio.sleep(CHECK_INTERVAL)

    @commands.slash_command(
        description="Назначить канал для уведомлений CS сервера",
        default_member_permissions=disnake.Permissions(administrator=True)
    )
    async def set_cs_server_channel(self, inter: disnake.ApplicationCommandInteraction, channel: disnake.TextChannel):
        _upsert_server(inter.guild.id, cs_server_channel_id=channel.id)
        await inter.response.send_message(f"✅ Канал CS сервера: {channel.mention}", ephemeral=True)

    @commands.slash_command(description="Статус CS2 сервера")
    async def server(self, inter: disnake.ApplicationCommandInteraction):
        await inter.response.defer()
        info, players = await get_server_info()
        if not info:
            return await inter.edit_original_response(content="❌ Сервер недоступен.")
        await inter.edit_original_response(embed=build_server_embed(info, players))

    @commands.slash_command(
        description="Открыть панель управления CS сервером",
        default_member_permissions=disnake.Permissions(administrator=True)
    )
    async def cs_admin(self, inter: disnake.ApplicationCommandInteraction):
        embed = disnake.Embed(
            title="⚙️ Панель управления CS2 сервером",
            description=f"`{CS_SERVER_HOST}:{CS_SERVER_PORT}`",
            color=disnake.Color.blurple()
        )
        await inter.response.send_message(embed=embed, view=AdminPanel(), ephemeral=True)


def setup(bot: commands.InteractionBot):
    bot.add_cog(GameServer(bot))
