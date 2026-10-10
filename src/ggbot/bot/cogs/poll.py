"""/poll: Umfragen mit Discords eigener Umfrage-Funktion.

Discord führt, zählt und beendet die Umfrage selbst (auch nach einem Neustart des Bots) und
postet das Ergebnis. Darum gibt es dafür keine eigenen Tabellen.
"""

import datetime as dt

import discord
from discord import app_commands
from discord.ext import commands

from ggbot.bot.checks import ensure_guild
from ggbot.bot.client import GGBot
from ggbot.bot.i18n import d
from ggbot.core import polls
from ggbot.core.errors import UserError
from ggbot.core.i18n import translator


class PollCog(commands.Cog):
    def __init__(self, bot: GGBot) -> None:
        self.bot = bot

    poll = app_commands.Group(
        name="poll",
        description=d("cmd.poll.desc"),
        guild_only=True,
        default_permissions=discord.Permissions(send_polls=True),
    )

    @poll.command(name="create", description=d("cmd.poll.create.desc"))
    @app_commands.describe(
        question=d("cmd.arg.poll_question.desc"),
        options=d("cmd.arg.poll_options.desc"),
        duration=d("cmd.arg.poll_duration.desc"),
        multiple=d("cmd.arg.poll_multiple.desc"),
    )
    async def create(
        self,
        interaction: discord.Interaction,
        question: str,
        options: str,
        duration: str = "24h",
        multiple: bool = False,
    ) -> None:
        ensure_guild(interaction)
        poll = discord.Poll(
            question=polls.check_question(question),
            duration=dt.timedelta(hours=polls.duration_hours(duration)),
            multiple=multiple,
        )
        for option in polls.parse_options(options):
            poll.add_answer(text=option)
        await interaction.response.send_message(poll=poll)

    @poll.command(name="end", description=d("cmd.poll.end.desc"))
    @app_commands.describe(message_id=d("cmd.arg.poll_message.desc"))
    async def end(self, interaction: discord.Interaction, message_id: str) -> None:
        ensure_guild(interaction)
        channel = interaction.channel
        if not message_id.isdigit() or channel is None or not hasattr(channel, "fetch_message"):
            raise UserError("error.poll_not_found")
        try:
            message = await channel.fetch_message(int(message_id))
        except discord.NotFound as exc:
            raise UserError("error.poll_not_found") from exc
        # Nur Umfragen dieses Bots: fremde zu beenden wäre nicht seine Sache.
        if message.poll is None or message.author.id != (self.bot.user.id if self.bot.user else 0):
            raise UserError("error.poll_not_found")
        try:
            await message.poll.end()
        except discord.HTTPException as exc:
            raise UserError("error.poll_not_found") from exc
        locale = await self.bot.services.guilds.get_locale(interaction.guild_id)
        await interaction.response.send_message(translator.t(locale, "poll.ended"), ephemeral=True)


async def setup(bot: GGBot) -> None:
    await bot.add_cog(PollCog(bot))
