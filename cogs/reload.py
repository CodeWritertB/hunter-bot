import disnake
import logging
from disnake.ext import commands

log = logging.getLogger("cogs.reload")

COGS = [
    "cogs.members", "cogs.help", "cogs.music", "cogs.voice_rooms",
    "cogs.logger", "cogs.xp", "cogs.info", "cogs.verify",
    "cogs.streaks", "cogs.tracker", "cogs.backup", "cogs.dota",
    "cogs.cs2", "cogs.gameserver", "cogs.admin_panel", "cogs.reload",
    "cogs.autoupdate",
]


class Reload(commands.Cog):
    def __init__(self, bot: commands.InteractionBot):
        self.bot = bot

    @commands.slash_command(
        description="Перезагрузить модуль бота без перезапуска",
        default_member_permissions=disnake.Permissions(administrator=True)
    )
    async def reload(
        self,
        inter: disnake.ApplicationCommandInteraction,
        module: str = commands.Param(
            description="Модуль для перезагрузки (или 'all' для всех)",
            default="all"
        )
    ):
        await inter.response.defer(ephemeral=True)

        if module == "all":
            success, failed = [], []
            for cog in COGS:
                try:
                    self.bot.reload_extension(cog)
                    success.append(cog.replace("cogs.", ""))
                except Exception as e:
                    failed.append(f"{cog.replace('cogs.', '')}: {e}")
                    log.error(f"Ошибка перезагрузки {cog}: {e}")

            result = f"✅ Перезагружено: {', '.join(success)}"
            if failed:
                result += f"\n❌ Ошибки:\n" + "\n".join(failed)
            log.info(f"[{inter.guild.name}] {inter.author} перезагрузил все модули")
        else:
            # Нормализуем имя
            cog_name = module if module.startswith("cogs.") else f"cogs.{module}"
            if cog_name not in COGS:
                return await inter.edit_original_response(
                    content=f"❌ Модуль `{module}` не найден.\nДоступные: {', '.join(c.replace('cogs.', '') for c in COGS)}"
                )
            try:
                self.bot.reload_extension(cog_name)
                result = f"✅ Модуль `{module}` перезагружен."
                log.info(f"[{inter.guild.name}] {inter.author} перезагрузил {cog_name}")
            except Exception as e:
                result = f"❌ Ошибка: {e}"
                log.error(f"Ошибка перезагрузки {cog_name}: {e}")

        await inter.edit_original_response(content=result)


def setup(bot: commands.InteractionBot):
    bot.add_cog(Reload(bot))
