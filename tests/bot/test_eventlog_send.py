import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import discord

from ggbot.bot.eventlog_send import LogSender, to_embed
from ggbot.core.logs.entries import LogEntry, LogField, LogFile

AT = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)


def entry(n: int = 1, *, file: LogFile | None = None) -> LogEntry:
    return LogEntry("voice", f"Titel {n}", 0x123456, AT, "Text", (LogField("A", "B"),), file)


def channel() -> MagicMock:
    mock = MagicMock(spec=discord.TextChannel)
    mock.id = 5
    mock.send = AsyncMock()
    return mock


async def flush(sender: LogSender) -> None:
    await asyncio.gather(*sender._workers.values())


def forbidden() -> discord.Forbidden:
    return discord.Forbidden(cast(Any, SimpleNamespace(status=403, reason="x")), "x")


def test_embed_enthaelt_alle_angaben() -> None:
    embed = to_embed(entry())
    assert embed.color is not None
    assert (embed.title, embed.description, embed.color.value) == ("Titel 1", "Text", 0x123456)
    assert embed.timestamp == AT
    assert [(f.name, f.value) for f in embed.fields] == [("A", "B")]
    assert to_embed(LogEntry("voice", "x", 1, AT)).description is None


async def test_eintraege_kommen_in_reihenfolge_an() -> None:
    sender, target = LogSender(), channel()
    for n in range(1, 4):
        sender.enqueue(target, entry(n), "de")
    await flush(sender)
    titles = [call.kwargs["embed"].title for call in target.send.await_args_list]
    assert titles == ["Titel 1", "Titel 2", "Titel 3"]


async def test_datei_wird_als_anhang_gesendet() -> None:
    sender, target = LogSender(), channel()
    sender.enqueue(target, entry(file=LogFile("a.txt", "Grüsse".encode())), "de")
    await flush(sender)
    sent = target.send.await_args.kwargs["file"]
    assert sent.filename == "a.txt"
    assert sent.fp.read().decode() == "Grüsse"


async def test_ueberlauf_wird_am_ende_in_einer_zeile_gemeldet() -> None:
    sender, target = LogSender(limit=3), channel()
    for n in range(10):
        sender.enqueue(target, entry(n), "de")
    await flush(sender)
    embeds = [c for c in target.send.await_args_list if "embed" in c.kwargs]
    assert len(embeds) == 3
    summary = target.send.await_args_list[-1].args[0]
    assert "7 Log-Einträge" in summary


async def test_ohne_schreibrecht_wird_der_rest_verworfen_nicht_endlos_versucht() -> None:
    sender, target = LogSender(), channel()
    target.send.side_effect = forbidden()
    for n in range(5):
        sender.enqueue(target, entry(n), "de")
    await flush(sender)
    assert target.send.await_count == 1  # ein Versuch, dann Schluss
    # Später (Recht zurück) funktioniert derselbe Kanal wieder.
    target.send.side_effect = None
    sender.enqueue(target, entry(9), "de")
    await flush(sender)
    # Nur der neue Eintrag, nicht die alten, die der Kanal damals nicht annehmen konnte.
    assert target.send.await_count == 2
    assert target.send.await_args.kwargs["embed"].title == "Titel 9"


async def test_anderer_sendefehler_stoppt_die_folgenden_nicht() -> None:
    sender, target = LogSender(), channel()
    broken = discord.HTTPException(cast(Any, SimpleNamespace(status=500, reason="x")), "kaputt")
    target.send.side_effect = [broken, None, None]
    for n in range(3):
        sender.enqueue(target, entry(n), "de")
    await flush(sender)
    assert target.send.await_count == 3


async def test_jeder_kanal_hat_seine_eigene_warteschlange() -> None:
    sender = LogSender(limit=1)
    a, b = channel(), channel()
    b.id = 6
    for target in (a, b):
        sender.enqueue(target, entry(1), "de")
        sender.enqueue(target, entry(2), "de")  # darüber: verworfen
    await asyncio.gather(*sender._workers.values())
    assert [c for c in a.send.await_args_list if "embed" in c.kwargs]
    assert [c for c in b.send.await_args_list if "embed" in c.kwargs]


async def test_aclose_wartet_auf_laufende_arbeit() -> None:
    sender, target = LogSender(), channel()
    sender.enqueue(target, entry(), "de")
    await sender.aclose()
    target.send.assert_awaited_once()
