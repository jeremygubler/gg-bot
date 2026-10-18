"""Ankündigungen aus dem Dashboard: senden und bearbeiten. Es gibt dafür keinen Slash-Befehl."""

import discord
from discord.ext import commands

from ggbot.bot.client import GGBot
from ggbot.core.announce import message as msg
from ggbot.core.db.models import ScheduledJob
from ggbot.core.db.models.announcements import STATE_SCHEDULED
from ggbot.core.logging import get_logger
from ggbot.core.services.announcements import SYNC_JOB, AnnouncementView

log = get_logger(__name__)


def embeds_of(view: AnnouncementView) -> list[discord.Embed]:
    embed = view.message.embed
    return [discord.Embed.from_dict(msg.embed_dict(embed))] if embed else []


class AnnouncementsCog(commands.Cog):
    def __init__(self, bot: GGBot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.services.scheduler.register(SYNC_JOB, self._job)

    async def _job(self, job: ScheduledJob) -> None:
        service = self.bot.services.announcements
        view = await service.get_for_job(int(job.payload["id"]))
        if view is None:
            return  # inzwischen gelöscht
        guild = self.bot.get_guild(view.guild_id)
        if guild is None:
            raise LookupError(f"Server {view.guild_id} nicht verfügbar")  # später erneut
        channel = guild.get_channel(view.channel_id or 0)
        if view.message_id is not None:
            await self._edit(view, channel)
        elif view.state == STATE_SCHEDULED and view.rev == int(job.payload["rev"]):
            await self._send(view, channel)
        # Sonst ist der Job veraltet (abgesagt oder neu geplant): nichts tun.

    def _channel_error(self, view: AnnouncementView, channel: object) -> str | None:
        if not isinstance(channel, discord.TextChannel):
            return "error.announce_channel_gone"
        have = channel.permissions_for(channel.guild.me)
        needs = have.view_channel and have.send_messages
        if view.message.embed is not None:
            needs = needs and have.embed_links
        return None if needs else "error.announce_perms"

    async def _send(self, view: AnnouncementView, channel: object) -> None:
        service = self.bot.services.announcements
        error = self._channel_error(view, channel)
        if error is None:
            assert isinstance(channel, discord.TextChannel)
            try:
                sent = await channel.send(
                    content=view.message.content,
                    embeds=embeds_of(view),
                    # Erwähnungen sind immer aus: wer @everyone will, schreibt es in Discord selbst.
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.Forbidden:
                error = "error.announce_perms"
            except discord.HTTPException:
                log.warning(
                    "announce_send_failed", guild_id=view.guild_id, id=view.id, exc_info=True
                )
                error = "error.announce_discord"
            else:
                if not await service.mark_sent(view.id, view.rev, sent.id):
                    # Zwischenzeitlich abgesagt oder gelöscht: die Kopie wieder zurücknehmen.
                    await sent.delete()
                return
        await service.mark_failed(view.id, view.rev, error)
        log.info("announce_failed", guild_id=view.guild_id, id=view.id, error=error)

    async def _edit(self, view: AnnouncementView, channel: object) -> None:
        service = self.bot.services.announcements
        if not isinstance(channel, discord.TextChannel) or view.message_id is None:
            await service.set_edit_result(view.id, "error.announce_channel_gone")
            return
        try:
            await channel.get_partial_message(view.message_id).edit(
                content=view.message.content,
                embeds=embeds_of(view),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.NotFound:
            await service.set_edit_result(view.id, "error.announce_message_gone")
        except discord.Forbidden:
            await service.set_edit_result(view.id, "error.announce_perms")
        except discord.HTTPException:
            log.warning("announce_edit_failed", guild_id=view.guild_id, id=view.id, exc_info=True)
            await service.set_edit_result(view.id, "error.announce_discord")
        else:
            await service.set_edit_result(view.id, None)


async def setup(bot: GGBot) -> None:
    await bot.add_cog(AnnouncementsCog(bot))
