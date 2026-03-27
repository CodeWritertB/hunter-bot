import disnake
import logging
import re
from disnake.ext import commands
from db.database import get_verify_settings, set_verify_settings

log = logging.getLogger("cogs.verify")

NAME_PATTERN = re.compile(r'^.+\s\(.+\)$')


class NicknameModal(disnake.ui.Modal):
    def __init__(self, guest_role_id: int, member_role_id: int):
        self.guest_role_id = guest_role_id
        self.member_role_id = member_role_id
        super().__init__(
            title="Изменить ник-нейм",
            components=[
                disnake.ui.TextInput(
                    label="Ник (Имя)",
                    custom_id="nickname",
                    placeholder="CodeWriter (Борис)",
                    max_length=32
                )
            ]
        )

    async def callback(self, inter: disnake.ModalInteraction):
        name = inter.text_values["nickname"].strip()
        if not NAME_PATTERN.match(name):
            return await inter.response.send_message(
                "❌ Неверный формат. Используй: **Ник (Имя)**\nПример: `CodeWriter (Борис)`",
                ephemeral=True
            )

        try:
            await inter.author.edit(nick=name)
        except disnake.Forbidden:
            pass

        guest_role = inter.guild.get_role(self.guest_role_id)
        member_role = inter.guild.get_role(self.member_role_id)
        if guest_role:
            await inter.author.remove_roles(guest_role)
        if member_role:
            await inter.author.add_roles(member_role)

        log.info(f"[{inter.guild.name}] {inter.author} верифицирован как '{name}'")
        await inter.response.send_message(
            f"✅ Добро пожаловать, **{name}**! Доступ к серверу открыт.",
            ephemeral=True
        )


class VerifyView(disnake.ui.View):
    def __init__(self, guest_role_id: int, member_role_id: int):
        super().__init__(timeout=None)
        self.guest_role_id = guest_role_id
        self.member_role_id = member_role_id

    @disnake.ui.button(label="Изменить ник-нейм", style=disnake.ButtonStyle.primary, emoji="✏️")
    async def verify_btn(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        await inter.response.send_modal(NicknameModal(self.guest_role_id, self.member_role_id))


class Verify(commands.Cog):
    def __init__(self, bot: commands.InteractionBot):
        self.bot = bot

    @commands.slash_command(
        description="Настроить систему верификации",
        default_member_permissions=disnake.Permissions(administrator=True)
    )
    async def set_verify(
        self,
        inter: disnake.ApplicationCommandInteraction,
        channel: disnake.TextChannel,
        guest_role: disnake.Role,
        member_role: disnake.Role
    ):
        set_verify_settings(inter.guild.id, channel.id, guest_role.id, member_role.id)
        log.info(f"[{inter.guild.name}] Верификация настроена: канал #{channel.name}, гость={guest_role.name}, участник={member_role.name}")

        # Отправляем постоянное сообщение с кнопкой в канал верификации
        embed = disnake.Embed(
            title="Верификация",
            description=(
                "Нажми на кнопку ниже чтобы изменить ник и получить доступ к серверу.\n\n"
                "Используй формат: **Ник (Имя)**\n"
                "Пример: `CodeWriter (Борис)`"
            ),
            color=disnake.Color.blurple()
        )
        await channel.send(embed=embed, view=VerifyView(guest_role.id, member_role.id))
        await inter.response.send_message(
            f"✅ Верификация настроена в {channel.mention}", ephemeral=True
        )

    @commands.Cog.listener()
    async def on_member_join(self, member: disnake.Member):
        settings = get_verify_settings(member.guild.id)
        if not settings:
            return
        channel_id, guest_role_id, member_role_id = settings

        guest_role = member.guild.get_role(guest_role_id)
        if guest_role:
            await member.add_roles(guest_role)

        channel = self.bot.get_channel(channel_id)
        if not channel:
            return

        await channel.send(
            f"{member.mention}, нажми на кнопку в этом канале чтобы изменить ник и получить доступ к серверу.",
            delete_after=60
        )
        log.info(f"[{member.guild.name}] Новый участник ожидает верификации: {member}")


def setup(bot: commands.InteractionBot):
    bot.add_cog(Verify(bot))
