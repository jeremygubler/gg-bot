"""Vorschläge auf Discord: Embed und Abstimmungsknöpfe.

Stimmen, Sperren und Entscheidungen liegen in `ggbot.core.services.suggestions`.
"""

from typing import Any

import discord

from ggbot.bot.panels import component_error
from ggbot.core.db.models.suggestions import STATUS_APPROVED, STATUS_DENIED
from ggbot.core.i18n import translator
from ggbot.core.services.suggestions import SuggestionView
from ggbot.core.suggestions import rules

VOTE_TEMPLATE = r"gg:sug:(?P<dir>up|down):(?P<id>\d+)"
COLORS = {
    STATUS_APPROVED: discord.Colour.green(),
    STATUS_DENIED: discord.Colour.red(),
}


def vote_id(direction: str, suggestion_id: int) -> str:
    return f"gg:sug:{direction}:{suggestion_id}"


def suggestion_embed(view: SuggestionView, tally: rules.Tally, locale: str) -> discord.Embed:
    embed = discord.Embed(
        title=translator.t(locale, "suggest.title", number=view.number),
        description=view.text,
        colour=COLORS.get(view.status, discord.Colour.blurple()),
    )
    embed.add_field(
        name=translator.t(locale, "suggest.author"), value=f"<@{view.author_id}>", inline=True
    )
    embed.add_field(
        name=translator.t(locale, "suggest.votes"),
        value=translator.t(locale, "suggest.tally", up=tally.up, down=tally.down),
        inline=True,
    )
    if view.status != "pending":
        embed.add_field(
            name=translator.t(locale, f"suggest.status.{view.status}"),
            value=view.decision_reason or "—",
            inline=False,
        )
    return embed


def suggestion_view(suggestion_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(VoteButton("up", suggestion_id))
    view.add_item(VoteButton("down", suggestion_id))
    return view


class VoteButton(discord.ui.DynamicItem[discord.ui.Button[Any]], template=VOTE_TEMPLATE):
    def __init__(self, direction: str, suggestion_id: int) -> None:
        self.direction = direction
        self.suggestion_id = suggestion_id
        super().__init__(
            discord.ui.Button(
                style=discord.ButtonStyle.success
                if direction == "up"
                else discord.ButtonStyle.danger,
                emoji="👍" if direction == "up" else "👎",
                custom_id=vote_id(direction, suggestion_id),
            )
        )

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: Any, /
    ) -> "VoteButton":
        return cls(match["dir"], int(match["id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        bot: Any = interaction.client
        services = bot.services
        if interaction.guild_id is None:
            return
        clicked = rules.UP if self.direction == "up" else rules.DOWN
        tally = await services.suggestions.vote(
            interaction.guild_id, self.suggestion_id, interaction.user.id, clicked
        )
        suggestion = await services.suggestions.get(interaction.guild_id, self.suggestion_id)
        if suggestion is None:
            return
        locale = await services.guilds.get_locale(interaction.guild_id)
        await interaction.response.edit_message(
            embed=suggestion_embed(suggestion, tally, locale),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await component_error(interaction, error)
