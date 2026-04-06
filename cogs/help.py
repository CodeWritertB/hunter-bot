import disnake
from disnake.ext import commands


class Help(commands.Cog):
    def __init__(self, bot: commands.InteractionBot):
        self.bot = bot

    @commands.slash_command(description="Список всех команд бота")
    async def help(self, inter: disnake.ApplicationCommandInteraction):
        # Embed 1 — общие команды
        embed1 = disnake.Embed(title="📖 Команды бота", color=disnake.Color.blurple())
        embed1.add_field(name="/help", value="Список всех команд.", inline=False)
        embed1.add_field(name="/rank [@участник]", value="Уровень и XP.", inline=False)
        embed1.add_field(name="/top", value="Топ по XP.", inline=False)
        embed1.add_field(name="/music [запрос]", value="Воспроизвести трек.", inline=False)
        embed1.add_field(name="/dota [@участник]", value="Статистика Dota 2.", inline=False)
        embed1.add_field(name="/dota_matches [@участник]", value="Последние матчи Dota 2.", inline=False)
        embed1.add_field(name="/dota_week", value="Матчи гильдии за неделю.", inline=False)
        embed1.add_field(name="/cs [@участник]", value="Статистика CS2 / Faceit.", inline=False)
        embed1.add_field(name="/link_steam [ссылка]", value="Привязать Steam аккаунт.", inline=False)
        embed1.add_field(name="/unlink_steam", value="Отвязать Steam аккаунт.", inline=False)
        embed1.add_field(name="/link_cs [faceit_ник]", value="Привязать Faceit для CS2.", inline=False)
        embed1.add_field(name="/server", value="Статус CS2 сервера.", inline=False)
        embed1.add_field(
            name="Голосовые комнаты",
            value="Зайди в лобби-канал — бот создаст комнату с панелью управления.",
            inline=False
        )

        embeds = [embed1]

        if inter.author.guild_permissions.administrator:
            embed2 = disnake.Embed(title="🔧 Админ команды", color=disnake.Color.red())
            embed2.add_field(name="/set_info [канал]", value="Канал оповещений о входе/выходе.", inline=False)
            embed2.add_field(name="/set_verify [канал] [гость] [участник]", value="Настроить верификацию.", inline=False)
            embed2.add_field(name="/set_new_channel [канал]", value="Лобби для приватных комнат.", inline=False)
            embed2.add_field(name="/set_music_channel [канал]", value="Канал для музыкального плеера.", inline=False)
            embed2.add_field(name="/set_dota_channel [канал]", value="Канал уведомлений Dota 2.", inline=False)
            embed2.add_field(name="/set_cs_channel [канал]", value="Канал уведомлений CS2.", inline=False)
            embed2.add_field(name="/set_cs_server_channel [канал]", value="Канал уведомлений CS2 сервера.", inline=False)
            embed2.add_field(name="/cs_admin", value="Панель управления CS2 сервером.", inline=False)
            embed2.add_field(name="/set_log [канал]", value="Канал логов сервера.", inline=False)
            embed2.add_field(name="/set_admin_log [канал]", value="Канал админских логов.", inline=False)
            embed2.add_field(name="/set_level_role [уровень] [роль]", value="Роль за уровень.", inline=False)
            embed2.add_field(name="/give_xp [@участник] [кол-во]", value="Выдать XP вручную.", inline=False)
            embed2.add_field(name="/room_takeover [канал]", value="Забрать управление комнатой.", inline=False)
            embed2.add_field(name="/clear [число]", value="Удалить до 100 сообщений.", inline=False)
            embed2.add_field(name="/userinfo [@участник]", value="Информация об участнике.", inline=False)
            embed2.add_field(name="/serverinfo", value="Статистика сервера.", inline=False)
            embed2.add_field(name="/send_message [@участник] [текст]", value="ЛС от имени бота.", inline=False)
            embeds.append(embed2)

        await inter.response.send_message(embeds=embeds, ephemeral=True)


def setup(bot: commands.InteractionBot):
    bot.add_cog(Help(bot))
