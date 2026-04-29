import asyncio
import logging
import subprocess
import os
from disnake.ext import commands

log = logging.getLogger("cogs.autoupdate")

CHECK_INTERVAL = 600  # Проверка каждые 10 минут

COGS = [
    "cogs.members", "cogs.help", "cogs.music", "cogs.voice_rooms",
    "cogs.logger", "cogs.xp", "cogs.info", "cogs.verify",
    "cogs.streaks", "cogs.tracker", "cogs.backup", "cogs.dota",
    "cogs.cs2", "cogs.gameserver", "cogs.admin_panel", "cogs.reload",
    "cogs.autoupdate",
]


def run_git(args: list[str]) -> tuple[int, str]:
    """Запускает git команду и возвращает (returncode, output)."""
    result = subprocess.run(
        ["git"] + args,
        capture_output=True, text=True,
        cwd=os.path.dirname(os.path.dirname(__file__))
    )
    return result.returncode, (result.stdout + result.stderr).strip()


class AutoUpdate(commands.Cog):
    def __init__(self, bot: commands.InteractionBot):
        self.bot = bot
        self.update_task = bot.loop.create_task(self.update_loop())

    def cog_unload(self):
        self.update_task.cancel()

    async def update_loop(self):
        """Каждые 10 минут проверяет обновления на GitHub."""
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            await asyncio.sleep(CHECK_INTERVAL)
            try:
                await self.check_and_update()
            except Exception as e:
                log.error(f"Ошибка в autoupdate: {e}")

    async def check_and_update(self):
        loop = asyncio.get_event_loop()

        # Получаем изменения с remote
        code, out = await loop.run_in_executor(None, lambda: run_git(["fetch"]))
        if code != 0:
            log.warning(f"git fetch failed: {out}")
            return

        # Проверяем есть ли новые коммиты
        code, out = await loop.run_in_executor(None, lambda: run_git(["rev-list", "HEAD..origin/main", "--count"]))
        if code != 0 or out.strip() == "0":
            return  # Нет обновлений

        log.info(f"Найдено {out.strip()} новых коммитов, обновляемся...")

        # Делаем pull
        code, out = await loop.run_in_executor(None, lambda: run_git(["pull", "--ff-only"]))
        if code != 0:
            log.error(f"git pull failed: {out}")
            return

        log.info(f"git pull: {out}")

        # Перезагружаем все модули
        success, failed = [], []
        for cog in COGS:
            try:
                self.bot.reload_extension(cog)
                success.append(cog.replace("cogs.", ""))
            except Exception as e:
                failed.append(f"{cog}: {e}")
                log.error(f"Ошибка перезагрузки {cog}: {e}")

        log.info(f"Автообновление завершено. Перезагружено: {', '.join(success)}")
        if failed:
            log.error(f"Ошибки перезагрузки: {'; '.join(failed)}")


def setup(bot: commands.InteractionBot):
    bot.add_cog(AutoUpdate(bot))
