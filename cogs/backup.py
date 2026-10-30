import asyncio
import logging
import os
import sqlite3
from datetime import datetime, timezone, timedelta
from disnake.ext import commands

log = logging.getLogger("cogs.backup")

from db.storage import backup_database

BACKUP_INTERVAL = 86400


def make_backup():
    path = backup_database()
    log.info("Бэкап создан и проверен: %s", path)
    return path


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
