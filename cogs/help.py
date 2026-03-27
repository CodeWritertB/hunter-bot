import disnake
from disnake.ext import commands


class Help(commands.Cog):
    def __init__(self, bot: commands.InteractionBot):
        self.bot = bot

    @commands.slash_command(description="Список всех команд бота")
    async def help(self, inter: disnake.ApplicationCommandInteraction):
        embed = disnake.Embed(title="📖 Команды бота", color=disnake.Color.blurple())

        embed.add_field(name="/help", value="Показать список всех команд.", inline=False)
        embed.add_field(name="/rank [@участник]", value="Посмотреть уровень и XP.", inline=False)
        embed.add_field(name="/top", value="Топ участников по XP.", inline=False)
        embed.add_field(name="/music [запрос]", value="Воспроизвести трек в голосовом канале.", inline=False)

        embed.add_field(
            name="Голосовые комнаты",
            value="Зайди в лобби-канал — бот создаст комнату и отправит панель управления прямо в канал.",
            inline=False
        )

        if inter.author.guild_permissions.administrator:
            embed.add_field(name="\u200b", value="**— Админ команды —**", inline=False)
            embed.add_field(name="/set_info [канал]", value="Канал оповещений о входе/выходе участников.", inline=False)
            embed.add_field(name="/set_music_channel [канал]", value="Канал для музыкального плеера.", inline=False)
            embed.add_field(name="/set_new_channel [канал]", value="Лобби-канал для приватных комнат.", inline=False)
            embed.add_field(name="/set_log [канал]", value="Канал для логов сервера.", inline=False)
            embed.add_field(name="/set_admin_log [канал]", value="Канал для логов админских действий.", inline=False)
            embed.add_field(name="/set_level_role [уровень] [роль]", value="Роль за достижение уровня.", inline=False)
            embed.add_field(name="/userinfo [@участник]", value="Информация об участнике.", inline=False)
            embed.add_field(name="/serverinfo", value="Информация о сервере.", inline=False)

        await inter.response.send_message(embed=embed, ephemeral=True)


def setup(bot: commands.InteractionBot):
    bot.add_cog(Help(bot))
