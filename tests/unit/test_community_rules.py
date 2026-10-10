from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import ValidationError

from ggbot.core.community import birthday, schedule, stats
from ggbot.core.community import starboard as sb
from ggbot.core.settings.birthday import BirthdaySettings
from ggbot.core.settings.starboard import StarboardSettings

# --- Starboard ---

BASE = {
    "voter_id": 2, "voter_is_bot": False, "author_id": 1, "channel_id": 10,
    "starboard_channel_id": 99, "ignored_channels": [], "self_star": False,
    "nsfw": False, "ignore_nsfw": True,
}  # fmt: skip


def counts(**kw: object) -> bool:
    return sb.counts(**(BASE | kw))  # type: ignore[arg-type]


def test_normaler_stern_zaehlt() -> None:
    assert counts()


def test_bots_zaehlen_nicht() -> None:
    assert not counts(voter_is_bot=True)


def test_eigener_stern_nur_wenn_erlaubt() -> None:
    assert not counts(voter_id=1)
    assert counts(voter_id=1, self_star=True)


def test_starboard_kanal_selbst_zaehlt_nie() -> None:
    # Sonst sammelten die Kopien wieder Sterne und kopierten sich ohne Ende.
    assert not counts(channel_id=99)
    assert not counts(channel_id=99, self_star=True)


def test_ignorierte_kanaele() -> None:
    assert not counts(ignored_channels=[10])
    assert counts(ignored_channels=[11])


def test_nsfw() -> None:
    assert not counts(nsfw=True)
    assert counts(nsfw=True, ignore_nsfw=False)
    assert counts(nsfw=False, ignore_nsfw=True)


@pytest.mark.parametrize(
    ("stars", "posted", "expected"),
    [
        (0, False, "none"),
        (2, False, "none"),
        (3, False, "post"),
        (9, False, "post"),
        (3, True, "update"),
        (7, True, "update"),
        (2, True, "remove"),
        (0, True, "remove"),
    ],
)
def test_aktion(stars: int, posted: bool, expected: str) -> None:
    assert sb.action(stars, 3, posted) == expected


def test_sternzeile() -> None:
    assert sb.star_line("⭐", 5, "<#1>") == "⭐ **5** | <#1>"


def test_starboard_einstellungen() -> None:
    assert StarboardSettings().enabled is False
    assert StarboardSettings().emoji == "⭐"
    ok = StarboardSettings.model_validate({"enabled": True, "channel_id": 5, "emoji": " 🌟 "})
    assert ok.emoji == "🌟"
    for bad in (
        {"enabled": True},  # ohne Kanal
        {"emoji": "stern"},
        {"emoji": ""},
        {"threshold": 0},
        {"threshold": 101},
        {"ignored_channels": list(range(51))},
    ):
        with pytest.raises(ValidationError):
            StarboardSettings.model_validate(bad)
    assert StarboardSettings.model_validate({"emoji": "<:x1:123456789012345678>"}).emoji


# --- Geburtstage ---


@pytest.mark.parametrize(
    ("day", "month", "ok"),
    [
        (1, 1, True), (31, 12, True), (29, 2, True), (30, 2, False), (31, 4, False),
        (30, 4, True), (0, 5, False), (32, 1, False), (1, 0, False), (1, 13, False), (-1, 1, False),
    ],
)  # fmt: skip
def test_gueltiges_datum(day: int, month: int, ok: bool) -> None:
    assert birthday.valid_date(day, month) is ok


def test_geburtstag_heute() -> None:
    assert birthday.is_birthday(14, 3, date(2026, 3, 14))
    assert not birthday.is_birthday(14, 3, date(2026, 3, 15))
    assert not birthday.is_birthday(14, 4, date(2026, 3, 14))


def test_schalttag() -> None:
    assert birthday.is_birthday(29, 2, date(2024, 2, 29))  # Schaltjahr: am 29.
    assert not birthday.is_birthday(29, 2, date(2024, 2, 28))
    assert birthday.is_birthday(29, 2, date(2025, 2, 28))  # sonst am 28.
    assert not birthday.is_birthday(29, 2, date(2025, 3, 1))
    assert birthday.is_birthday(28, 2, date(2025, 2, 28))  # der 28. bleibt der 28.


def test_lokales_datum_nach_zeitzone() -> None:
    now = datetime(2026, 3, 14, 23, 30, tzinfo=UTC)
    assert birthday.local_today(now, "UTC") == date(2026, 3, 14)
    assert birthday.local_today(now, "Europe/Zurich") == date(2026, 3, 15)  # schon nach Mitternacht
    assert birthday.local_today(now, "America/Los_Angeles") == date(2026, 3, 14)


def test_geburtstags_einstellungen() -> None:
    assert BirthdaySettings().enabled is False
    BirthdaySettings.model_validate(
        {"enabled": True, "channel_id": 1, "message": "Hi {user} {name} {server}"}
    )
    for bad in (
        {"enabled": True},
        {"message": "{level}"},
        {"message": "{user.__class__}"},
        {"message": "x" * 301},
    ):
        with pytest.raises(ValidationError):
            BirthdaySettings.model_validate(bad)


# --- Stat-Kanäle ---

SNAP = stats.Snapshot(member_count=1234, bots=34, channels=20, roles=9, boosts=7)


def test_werte() -> None:
    assert stats.value("members", SNAP) == 1234
    assert stats.value("bots", SNAP) == 34
    assert stats.value("humans", SNAP) == 1200
    assert stats.value("channels", SNAP) == 20
    assert stats.value("roles", SNAP) == 9
    assert stats.value("boosts", SNAP) == 7
    assert stats.value("unbekannt", SNAP) is None


def test_ohne_mitgliederliste_keine_bot_zahlen() -> None:
    snap = stats.Snapshot(100, None, 1, 1, 0)
    assert stats.value("bots", snap) is None
    assert stats.value("humans", snap) is None
    assert stats.value("members", snap) == 100  # die Gesamtzahl kennt Discord immer


def test_menschen_werden_nie_negativ() -> None:
    assert stats.value("humans", stats.Snapshot(5, 9, 0, 0, 0)) == 0


def test_name_aus_vorlage() -> None:
    assert stats.render_name("Mitglieder: {count}", 1234) == "Mitglieder: 1.234"
    assert stats.render_name("{count}", 7) == "7"


@pytest.mark.parametrize(
    "template",
    [
        "Mitglieder",
        "{count} {level}",
        "{count.real}",
        "{count!r}",
        "{",
        "{count:>9}",
        "x" * 120 + "{count}",
    ],
)
def test_ungueltige_namensvorlage(template: str) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        stats.validate_template(template)


def test_gueltige_namensvorlage() -> None:
    stats.validate_template("👥 Mitglieder: {count}")
    stats.validate_template("x" * 70 + "{count}")


def test_arten_und_mitglieder_abhaengigkeit() -> None:
    assert set(stats.KINDS) == {"members", "humans", "bots", "channels", "roles", "boosts"}
    assert {"humans", "bots"} == stats.NEEDS_MEMBERS


# --- Zeitplan ---

T0 = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
HOUR = timedelta(hours=1)


def test_naechster_termin_normal() -> None:
    assert schedule.next_run(T0, HOUR, T0 + timedelta(seconds=5)) == T0 + HOUR


def test_verpasste_runden_werden_uebersprungen() -> None:
    now = T0 + timedelta(hours=5, minutes=20)
    assert schedule.next_run(T0, HOUR, now) == T0 + timedelta(hours=6)  # im alten Raster


def test_genau_auf_dem_raster() -> None:
    assert schedule.next_run(T0, HOUR, T0 + HOUR) == T0 + 2 * HOUR


def test_termin_liegt_immer_nach_jetzt() -> None:
    for minutes in (0, 59, 60, 61, 119, 120, 5000):
        now = T0 + timedelta(minutes=minutes)
        result = schedule.next_run(T0, HOUR, now)
        assert result > now
        assert (result - T0) % HOUR == timedelta(0)


@pytest.mark.parametrize("interval", [timedelta(0), timedelta(seconds=-5)])
def test_intervall_muss_positiv_sein(interval: timedelta) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        schedule.next_run(T0, interval, T0)
