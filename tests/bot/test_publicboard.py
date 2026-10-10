"""Öffentliche Rangliste: was der Bot in den Zwischenspeicher legt und wann."""

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from redis.asyncio import Redis

from ggbot.bot.client import GGBot
from ggbot.bot.cogs import publicboard
from ggbot.bot.cogs.publicboard import PublicBoardCog
from ggbot.bot.services import Services
from ggbot.core import events
from ggbot.core.config import AppConfig
from ggbot.core.db.engine import Database
from ggbot.core.publicboard import PublicBoard, load_board, save_board
from ggbot.core.settings.leveling import LevelingSettings

GUILD = 123456789012345678


@pytest.fixture
async def bot(db: Database, redis: Redis) -> GGBot:
    return GGBot(Services.build(AppConfig(), db, redis))


def member(user_id: int, name: str) -> MagicMock:
    m = MagicMock()
    m.id = user_id
    m.display_name = name
    avatar = MagicMock()
    avatar.replace.return_value.url = f"https://cdn.example/{user_id}.png"
    m.display_avatar = avatar
    return m


def guild_with(*known: MagicMock) -> MagicMock:
    guild = MagicMock()
    guild.id = GUILD
    guild.name = "Alpha"
    table = {m.id: m for m in known}
    guild.get_member = MagicMock(side_effect=table.get)
    guild.fetch_member = AsyncMock(side_effect=discord.NotFound(MagicMock(status=404), "gone"))
    return guild


async def settings(bot: GGBot, *, enabled: bool = True, public: bool = True) -> None:
    await bot.services.settings.save(
        GUILD, LevelingSettings(enabled=enabled, public_leaderboard=public)
    )
    bot.services.leveling.invalidate(GUILD)


async def xp(bot: GGBot, **users: int) -> None:
    for user_id, amount in users.items():
        await bot.services.leveling.give(GUILD, int(user_id[1:]), amount)


async def test_baut_die_liste_mit_namen_und_avataren(bot: GGBot) -> None:
    await settings(bot)
    await xp(bot, u1=500, u2=300)
    guild = guild_with(member(1, "Alice"), member(2, "Bob"))

    await PublicBoardCog(bot).refresh(guild)

    board = await load_board(bot.services.redis, GUILD)
    assert board is not None
    assert board.guild_name == "Alpha"
    assert [(r.position, r.name, r.xp) for r in board.rows] == [(1, "Alice", 500), (2, "Bob", 300)]
    assert board.rows[0].avatar_url == "https://cdn.example/1.png"
    assert board.rows[0].level >= 1


async def test_ehemalige_mitglieder_fehlen_in_der_oeffentlichen_liste(bot: GGBot) -> None:
    await settings(bot)
    await xp(bot, u1=500, u2=300, u3=100)
    guild = guild_with(member(1, "Alice"), member(3, "Carl"))  # 2 ist gegangen (NotFound)

    await PublicBoardCog(bot).refresh(guild)

    board = await load_board(bot.services.redis, GUILD)
    assert board is not None
    assert [(r.position, r.name) for r in board.rows] == [(1, "Alice"), (3, "Carl")]


async def test_nachfragen_sind_pro_lauf_begrenzt_und_der_rest_bleibt_unbenannt(
    bot: GGBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(publicboard, "MAX_FETCH_PER_RUN", 2)
    await settings(bot)
    await xp(bot, u1=500, u2=400, u3=300, u4=200)
    guild = guild_with()
    guild.fetch_member = AsyncMock(side_effect=lambda uid: member(uid, f"N{uid}"))

    await PublicBoardCog(bot).refresh(guild)

    board = await load_board(bot.services.redis, GUILD)
    assert board is not None
    assert guild.fetch_member.await_count == 2
    assert [r.name for r in board.rows] == ["N1", "N2", None, None]
    assert board.rows[2].avatar_url is None


async def test_discord_fehler_beim_nachfragen_bleibt_ohne_namen_statt_zu_fehlen(bot: GGBot) -> None:
    await settings(bot)
    await xp(bot, u1=500)
    guild = guild_with()
    guild.fetch_member = AsyncMock(side_effect=discord.HTTPException(MagicMock(status=500), "x"))

    await PublicBoardCog(bot).refresh(guild)

    board = await load_board(bot.services.redis, GUILD)
    assert board is not None
    assert [(r.position, r.name) for r in board.rows] == [(1, None)]


@pytest.mark.parametrize(("enabled", "public"), [(True, False), (False, True)])
async def test_ohne_freigabe_wird_nichts_gebaut_und_vorhandenes_geloescht(
    bot: GGBot, enabled: bool, public: bool
) -> None:
    await settings(bot, enabled=enabled, public=public)
    await xp(bot, u1=500)
    await save_board(bot.services.redis, GUILD, PublicBoard(guild_name="alt", rows=[]))

    await PublicBoardCog(bot).refresh(guild_with(member(1, "Alice")))

    assert await load_board(bot.services.redis, GUILD) is None


async def test_aenderung_der_einstellung_loest_sofort_aus(bot: GGBot) -> None:
    await settings(bot)
    await xp(bot, u1=500)
    guild = guild_with(member(1, "Alice"))
    bot.get_guild = MagicMock(return_value=guild)  # type: ignore[method-assign]
    cog = PublicBoardCog(bot)

    await cog.on_ggbot_settings_changed(events.SettingsChanged(guild_id=GUILD, module="leveling"))

    assert await load_board(bot.services.redis, GUILD) is not None


async def test_andere_module_loesen_nichts_aus(bot: GGBot) -> None:
    await settings(bot)
    bot.get_guild = MagicMock(side_effect=AssertionError("darf nicht gefragt werden"))  # type: ignore[method-assign]

    await PublicBoardCog(bot).on_ggbot_settings_changed(
        events.SettingsChanged(guild_id=GUILD, module="welcome")
    )


async def test_ein_fehlschlag_haelt_die_uebrigen_server_nicht_auf(bot: GGBot) -> None:
    cog = PublicBoardCog(bot)
    broken = MagicMock()
    broken.id = 1
    healthy = guild_with(member(1, "Alice"))
    await settings(bot)
    await xp(bot, u1=500)
    calls: list[int] = []
    original = cog.refresh

    async def flaky(guild: discord.Guild) -> None:
        calls.append(guild.id)
        if guild is broken:
            raise RuntimeError("kaputt")
        await original(guild)

    cog.refresh = flaky  # type: ignore[method-assign]

    await cog._guarded(broken)
    await cog._guarded(healthy)

    assert calls == [1, GUILD]
    assert await load_board(bot.services.redis, GUILD) is not None


async def test_zwischenspeicher_laeuft_ab(bot: GGBot) -> None:
    await settings(bot)
    await save_board(bot.services.redis, GUILD, PublicBoard(guild_name="x", rows=[]))

    ttl = await bot.services.redis.ttl(f"gg:publicboard:{GUILD}")

    assert 0 < ttl <= 2 * 3600


def test_cog_ist_registriert() -> None:
    from ggbot.bot.client import COGS

    assert "ggbot.bot.cogs.publicboard" in COGS
