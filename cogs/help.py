import disnake
from disnake.ext import commands


class Help(commands.Cog):
    def __init__(self, bot: commands.InteractionBot):
        self.bot = bot

    @commands.slash_command(description="Список всех команд бота")
    async def help(self, inter: disnake.ApplicationCommandInteraction):
        embed = disnake.Embed(title="📖 Команды бота", color=disnake.Color.blurple())

        # Общие команды
        embed.add_field(name="/help", value="Показать список всех команд.", inline=False)
        embed.add_field(name="/rank [@участник]", value="Посмотреть уровень и XP.", inline=False)
        embed.add_field(name="/top", value="Топ участников по XP.", inline=False)
        embed.add_field(name="/music [запрос]", value="Воспроизвести трек в голосовом канале.", inline=False)
        embed.add_field(name="/dota [@участник]", value="Статистика Dota 2.", inline=False)
        embed.add_field(name="/dota_matches [@участник]", value="Последние матчи Dota 2.", inline=False)
        embed.add_field(name="/dota_week", value="Матчи гильдии за неделю.", inline=False)
        embed.add_field(name="/cs [@участник]", value="Статистика CS2 / Faceit.", inline=False)
        embed.add_field(name="/link_steam [ссылка]", value="Привязать Steam аккаунт.", inline=False)
        embed.add_field(name="/unlink_steam", value="Отвязать Steam аккаунт.", inline=False)
        embed.add_field(name="/link_cs [faceit_ник]", value="Привязать Faceit аккаунт для CS2.", inline=False)

        embed.add_field(
            name="Голосовые комнаты",
            value="Зайди в лобби-канал — бот создаст комнату с панелью управления.",
            inline=False
        )

        if inter.author.guild_permissions.administrator:
            embed.add_field(name="\u200b", value="**— Админ команды —**", inline=False)
            embed.add_field(name="/set_info [канал]", value="Канал оповещений о входе/выходе участников.", inline=False)
            embed.add_field(name="/set_verify [канал] [гость] [участник]", value="Настроить систему верификации.", inline=False)
            embed.add_field(name="/set_new_channel [канал]", value="Лобби-канал для приватных голосовых комнат.", inline=False)
            embed.add_field(name="/set_music_channel [канал]", value="Канал для музыкального плеера.", inline=False)
            embed.add_field(name="/set_dota_channel [канал]", value="Канал уведомлений о матчах Dota 2.", inline=False)
            embed.add_field(name="/set_cs_channel [канал]", value="Канал уведомлений о матчах CS2.", inline=False)
            embed.add_field(name="/set_log [канал]", value="Канал для логов сервера.", inline=False)
            embed.add_field(name="/set_admin_log [канал]", value="Канал для логов админских действий.", inline=False)
            embed.add_field(name="/set_level_role [уровень] [роль]", value="Роль за достижение уровня.", inline=False)
            embed.add_field(name="/give_xp [@участник] [кол-во]", value="Выдать XP вручную.", inline=False)
            embed.add_field(name="/room_takeover [канал]", value="Забрать управление голосовой комнатой.", inline=False)
            embed.add_field(name="/clear [число]", value="Удалить до 100 сообщений в канале.", inline=False)
            embed.add_field(name="/userinfo [@участник]", value="Подробная информация об участнике.", inline=False)
            embed.add_field(name="/serverinfo", value="Статистика сервера.", inline=False)
            embed.add_field(name="/send_message [@участник] [текст]", value="Отправить ЛС от имени бота.", inline=False)

        await inter.response.send_message(embed=embed, ephemeral=True)


def setup(bot: commands.InteractionBot):
    bot.add_cog(Help(bot))
