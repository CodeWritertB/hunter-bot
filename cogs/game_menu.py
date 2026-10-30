"""Persistent game menus using the same handlers as slash commands."""
import logging
import disnake

log = logging.getLogger(__name__)


class PrivateResponse:
    def __init__(self, response):
        self.response = response

    def __getattr__(self, name):
        return getattr(self.response, name)

    async def defer(self, **kwargs):
        kwargs.update(ephemeral=True, with_message=True)
        return await self.response.defer(**kwargs)

    async def send_message(self, *args, **kwargs):
        kwargs["ephemeral"] = True
        return await self.response.send_message(*args, **kwargs)


class PrivateInteraction:
    def __init__(self, inter):
        self.inter = inter
        self.response = PrivateResponse(inter.response)

    def __getattr__(self, name):
        return getattr(self.inter, name)


async def invoke(inter, game, action, **kwargs):
    cog = inter.bot.get_cog("Dota" if game == "dota" else "CS2")
    if not cog:
        return await inter.response.send_message("Модуль временно недоступен. Попробуй позже.", ephemeral=True)
    try:
        command = getattr(type(cog), action)
        await command.callback(cog, PrivateInteraction(inter), **kwargs)
    except Exception:
        log.exception("Ошибка игровой панели: %s/%s", game, action)
        if inter.response.is_done():
            await inter.followup.send("Не удалось получить данные. Попробуй позже или сообщи администратору.", ephemeral=True)
        else:
            await inter.response.send_message("Не удалось получить данные. Попробуй позже.", ephemeral=True)


class LinkModal(disnake.ui.Modal):
    def __init__(self, game):
        self.game = game
        super().__init__(title="Привязать Steam" if game == "dota" else "Привязать FACEIT", components=[
            disnake.ui.TextInput(label="Ссылка Steam или Steam ID" if game == "dota" else "Ник FACEIT",
                                 custom_id="account", max_length=200,
                                 placeholder="https://steamcommunity.com/id/..." if game == "dota" else "Твой ник на FACEIT")])

    async def callback(self, inter):
        value = inter.text_values["account"].strip()
        await invoke(inter, self.game, "link_steam" if self.game == "dota" else "link_cs",
                     **({"steam": value} if self.game == "dota" else {"faceit_nickname": value}))


class ActionButton(disnake.ui.Button):
    def __init__(self, game, action, label, emoji, primary=False):
        self.game, self.action = game, action
        super().__init__(label=label, emoji=emoji, custom_id=f"game:{game}:{action}",
                         style=disnake.ButtonStyle.primary if primary else disnake.ButtonStyle.secondary)

    async def callback(self, inter):
        if self.action == "link":
            return await inter.response.send_modal(LinkModal(self.game))
        await invoke(inter, self.game, self.action)


class GameMenu(disnake.ui.View):
    def __init__(self, game):
        super().__init__(timeout=None)
        self.add_item(ActionButton(game, "link", "Привязать аккаунт", "🔗", True))
        self.add_item(ActionButton(game, "dota" if game == "dota" else "cs", "Моя статистика", "📊"))
        if game == "dota":
            self.add_item(ActionButton(game, "dota_matches", "Последние матчи", "⚔️"))
            self.add_item(ActionButton(game, "dota_week", "Неделя сервера", "🏆"))
