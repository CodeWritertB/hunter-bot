import asyncio
import logging
import os
import shutil
from datetime import datetime, timezone, timedelta
from disnake.ext import commands

log = logging.getLogger("cogs.backup")

# Путь к БД и папке бэкапов
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "bot.db")
BACKUP_DIR = os.path.join(os.path.dirname(__file__), "..", "backups")

BACKUP_INTERVAL = 86400  # Интервал бэкапа — 24 часа
MAX_BACKUPS = 7          # Максимальное количество хранимых бэкапов


def make_backup():
    """Создаёт копию bot.db с датой в имени файла."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    date_str = datetime.now(timezone(timedelta(hours=3))).strftime("%Y-%m-%d_%H-%M")
    backup_path = os.path.join(BACKUP_DIR, f"bot_{date_str}.db")
    shutil.copy2(DB_PATH, backup_path)
    log.info(f"Бэкап создан: {backup_path}")
    cleanup_old_backups()


def cleanup_old_backups():
    """Удаляет старые бэкапы, оставляя только MAX_BACKUPS последних."""
    files = sorted(
        [f for f in os.listdir(BACKUP_DIR) if f.endswith(".db")],
        reverse=True
    )
    for old_file in files[MAX_BACKUPS:]:
        os.remove(os.path.join(BACKUP_DIR, old_file))
        log.info(f"Старый бэкап удалён: {old_file}")


class Backup(commands.Cog):
    def __init__(self, bot: commands.InteractionBot):
        self.bot = bot
        self.backup_task = bot.loop.create_task(self.backup_loop())

    def cog_unload(self):
        self.backup_task.cancel()

    async def backup_loop(self):
        """Цикл автобэкапа — запускается каждые 24 часа."""
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            await asyncio.sleep(BACKUP_INTERVAL)
            try:
                make_backup()
            except Exception as e:
                log.error(f"Ошибка при создании бэкапа: {e}")


def setup(bot: commands.InteractionBot):
    bot.add_cog(Backup(bot))
