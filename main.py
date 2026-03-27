import disnake
import os
import logging
import asyncio
from dotenv import load_dotenv
from disnake.ext import commands

# Настройка логирования в консоль
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("bot")

# Глушим лишние логи от disnake — оставляем только WARNING и выше
logging.getLogger("disnake").setLevel(logging.WARNING)
logging.getLogger("disnake.gateway").setLevel(logging.WARNING)
logging.getLogger("disnake.client").setLevel(logging.WARNING)
logging.getLogger("disnake.http").setLevel(logging.WARNING)
logging.getLogger("disnake.voice_client").setLevel(logging.WARNING)

# Загружаем токен из .env файла
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

# Создаём бота только с поддержкой slash-команд
bot = commands.InteractionBot(intents=disnake.Intents.all())

# Загружаем все модули (cogs)
bot.load_extension("cogs.members")      # Оповещения о входе/выходе
bot.load_extension("cogs.help")         # Команда /help
bot.load_extension("cogs.music")        # Музыкальный плеер
bot.load_extension("cogs.voice_rooms")  # Приватные голосовые комнаты
bot.load_extension("cogs.logger")       # Логирование событий сервера
bot.load_extension("cogs.xp")           # Система XP и уровней
bot.load_extension("cogs.info")         # /userinfo, /serverinfo
bot.load_extension("cogs.verify")       # Верификация новых участников
bot.load_extension("cogs.streaks")      # Стрики активности в войсе
bot.load_extension("cogs.tracker")      # Сбор статистики участников
bot.load_extension("cogs.backup")       # Автобэкап базы данных
bot.load_extension("cogs.dota")         # Dota 2 / Steam статистика
bot.load_extension("cogs.cs2")          # CS2 / Faceit статистика


async def status_loop():
    """Цикл динамического статуса бота — обновляется каждые 15 секунд."""
    from cogs.voice_rooms import panel_messages
    from cogs.music import players

    idx = 0
    await bot.wait_until_ready()
    while not bot.is_closed():
        active_rooms = len(panel_messages)

        # Считаем сколько человек сейчас в приватных комнатах
        people_in_rooms = sum(
            len(bot.get_channel(cid).members)
            for cid in panel_messages
            if bot.get_channel(cid)
        ) if panel_messages else 0

        # Ищем текущий играющий трек среди всех плееров
        current_track = None
        for player in players.values():
            if player.current and player.current.get("title"):
                current_track = player.current["title"]
                break

        # Формируем список статусов для чередования
        statuses = [
            (disnake.ActivityType.watching, f"{active_rooms} комнат | {people_in_rooms} человек в комнатах"),
        ]
        if current_track:
            statuses.append((disnake.ActivityType.listening, current_track))

        atype, text = statuses[idx % len(statuses)]
        await bot.change_presence(activity=disnake.Activity(type=atype, name=text))
        idx += 1
        await asyncio.sleep(15)


@bot.event
async def on_ready():
    log.info(f"Bot {bot.user} is ready to work!")
    # Запускаем цикл обновления статуса
    bot.loop.create_task(status_loop())

bot.run(os.getenv("BOT_TOKEN"))
