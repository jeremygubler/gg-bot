import pytest
from redis.asyncio import Redis

from ggbot.core.db.engine import Database
from ggbot.core.services.eventlog import EventLogService, required_intent
from ggbot.core.settings.eventlog import EVENT_TYPES, LoggingSettings
from ggbot.core.settings.store import SettingsStore

G = 100


@pytest.fixture
async def setup(db: Database, redis: Redis) -> tuple[EventLogService, SettingsStore]:
    store = SettingsStore(db, redis)
    return EventLogService(store), store


async def configure(
    setup: tuple[EventLogService, SettingsStore], **kwargs: object
) -> EventLogService:
    service, store = setup
    await store.save(G, LoggingSettings.model_validate(kwargs))
    service.invalidate(G)
    return service


async def test_ohne_einstellung_wird_nichts_protokolliert(
    setup: tuple[EventLogService, SettingsStore],
) -> None:
    service, _ = setup
    for event in EVENT_TYPES:
        assert await service.route(G, event) is None


async def test_jede_art_geht_in_ihren_eigenen_kanal(
    setup: tuple[EventLogService, SettingsStore],
) -> None:
    service = await configure(setup, channels={"voice": 11, "member_ban": 22})
    assert await service.route(G, "voice") == 11
    assert await service.route(G, "member_ban") == 22
    assert await service.route(G, "member_join") is None


async def test_server_sind_getrennt(setup: tuple[EventLogService, SettingsStore]) -> None:
    service = await configure(setup, channels={"voice": 11})
    assert await service.route(G + 1, "voice") is None


async def test_bots_werden_bei_nachrichten_und_sprache_ignoriert_sonst_nicht(
    setup: tuple[EventLogService, SettingsStore],
) -> None:
    service = await configure(
        setup, channels={"message_delete": 1, "voice": 2, "member_join": 3, "role_create": 4}
    )
    assert await service.route(G, "message_delete", is_bot=True) is None
    assert await service.route(G, "voice", is_bot=True) is None
    assert await service.route(G, "member_join", is_bot=True) == 3  # ein Bot-Beitritt ist relevant
    assert await service.route(G, "message_delete", is_bot=False) == 1
    service = await configure(setup, channels={"voice": 2}, ignore_bots=False)
    assert await service.route(G, "voice", is_bot=True) == 2


async def test_ignorierte_kanaele_gelten_nur_fuer_nachrichten(
    setup: tuple[EventLogService, SettingsStore],
) -> None:
    service = await configure(
        setup, channels={"message_edit": 1, "message_bulk": 1, "voice": 2}, ignored_channels=[50]
    )
    assert await service.route(G, "message_edit", channel_id=50) is None
    assert await service.route(G, "message_bulk", channel_id=50) is None
    assert await service.route(G, "message_edit", channel_id=51) == 1
    assert await service.route(G, "voice", channel_id=50) == 2


async def test_loeschen_im_log_kanal_selbst_wird_nicht_protokolliert(
    setup: tuple[EventLogService, SettingsStore],
) -> None:
    service = await configure(setup, channels={"message_delete": 1, "voice": 2})
    # Würde ein Moderator einen Log-Eintrag löschen, entstünde sonst ein Eintrag über den Eintrag.
    assert await service.route(G, "message_delete", channel_id=1) is None
    assert await service.route(G, "message_delete", channel_id=2) is None
    assert await service.route(G, "message_delete", channel_id=3) == 1


async def test_einstellungen_sind_gecacht_bis_zur_invalidierung(
    setup: tuple[EventLogService, SettingsStore],
) -> None:
    service = await configure(setup, channels={"voice": 11})
    assert await service.route(G, "voice") == 11
    _, store = setup
    await store.save(G, LoggingSettings())
    assert await service.route(G, "voice") == 11  # noch der Prozess-Cache
    service.invalidate(G)
    assert await service.route(G, "voice") is None


def test_benoetigte_intents() -> None:
    assert required_intent("message_delete") == "message_content"
    assert required_intent("message_bulk") == "message_content"
    assert required_intent("member_join") == "members"
    assert required_intent("member_update") == "members"
    for event in (
        "member_ban",
        "member_unban",
        "role_create",
        "channel_update",
        "voice",
        "invite_create",
    ):
        assert required_intent(event) is None
