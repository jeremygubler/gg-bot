from datetime import UTC, datetime, timedelta

from ggbot.core.purge import MessageInfo, PurgeFilter, select_for_purge

NOW = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)


def msg(
    id: int,
    *,
    author: int = 1,
    bot: bool = False,
    content: str = "hallo",
    attachments: bool = False,
    embeds: bool = False,
    pinned: bool = False,
    age: timedelta | None = None,
) -> MessageInfo:
    age = age if age is not None else timedelta(minutes=id)
    return MessageInfo(id, author, bot, content, attachments, embeds, NOW - age, pinned)


def ids(messages: list[MessageInfo]) -> list[int]:
    return [m.id for m in messages]


def test_ohne_filter_nimmt_die_juengsten() -> None:
    messages = [msg(i) for i in range(1, 6)]
    assert ids(select_for_purge(messages, PurgeFilter(), amount=3, now=NOW)) == [1, 2, 3]


def test_filter_nach_autor() -> None:
    messages = [msg(1, author=1), msg(2, author=2), msg(3, author=1)]
    selected = select_for_purge(messages, PurgeFilter(user_id=1), amount=10, now=NOW)
    assert ids(selected) == [1, 3]


def test_filter_enthaelt_text_ohne_gross_kleinschreibung() -> None:
    messages = [msg(1, content="Das ist SPAM"), msg(2, content="nett")]
    assert ids(select_for_purge(messages, PurgeFilter(contains="spam"), amount=10, now=NOW)) == [1]


def test_filter_bots_anhaenge_links_embeds() -> None:
    messages = [
        msg(1, bot=True),
        msg(2, attachments=True),
        msg(3, content="schau https://example.com/x"),
        msg(4, embeds=True),
        msg(5, content="kein link, nur example.com"),
    ]
    assert ids(select_for_purge(messages, PurgeFilter(bots_only=True), amount=9, now=NOW)) == [1]
    assert ids(
        select_for_purge(messages, PurgeFilter(attachments_only=True), amount=9, now=NOW)
    ) == [2]
    assert ids(select_for_purge(messages, PurgeFilter(links_only=True), amount=9, now=NOW)) == [3]
    assert ids(select_for_purge(messages, PurgeFilter(embeds_only=True), amount=9, now=NOW)) == [4]


def test_filter_kombiniert_alle_bedingungen() -> None:
    messages = [msg(1, author=1, bot=True), msg(2, author=1), msg(3, author=2, bot=True)]
    flt = PurgeFilter(user_id=1, bots_only=True)
    assert ids(select_for_purge(messages, flt, amount=9, now=NOW)) == [1]


def test_zu_alte_nachrichten_werden_nicht_angefasst() -> None:
    fresh = msg(1, age=timedelta(days=13, hours=23))
    old = msg(2, age=timedelta(days=14, minutes=5))
    older = msg(3, age=timedelta(days=30))
    assert ids(select_for_purge([fresh, old, older], PurgeFilter(), amount=9, now=NOW)) == [1]


def test_nichts_passt() -> None:
    assert select_for_purge([msg(1)], PurgeFilter(contains="xyz"), amount=5, now=NOW) == []


def test_angeheftete_nachrichten_bleiben() -> None:
    messages = [msg(1, pinned=True), msg(2)]
    assert ids(select_for_purge(messages, PurgeFilter(), amount=5, now=NOW)) == [2]
