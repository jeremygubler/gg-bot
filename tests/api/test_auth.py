import httpx

from ggbot.api.discord import INVITE_PERMISSIONS
from ggbot.api.sessions import COOKIE_NAME, STATE_COOKIE
from tests.api.conftest import (
    BIG_CHANNEL,
    CSRF,
    GUILD_A,
    GUILD_C,
    USER_ID,
    Api,
    FakeDiscord,
    login,
)


async def test_login_leitet_zu_discord_mit_state(api: Api) -> None:
    response = await api.client.get("/auth/login")

    assert response.status_code == 302
    url = httpx.URL(response.headers["location"])
    assert url.host == "discord.com"
    assert url.params["client_id"] == "1234"
    assert url.params["scope"] == "identify guilds"
    assert url.params["redirect_uri"] == "https://bot.example.org/api/auth/callback"
    cookie = response.headers["set-cookie"].lower()
    assert STATE_COOKIE in cookie
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert "secure" in cookie


async def test_callback_legt_sitzung_an_und_me_zeigt_nur_verwaltbare_server(api: Api) -> None:
    await login(api)

    me = (await api.client.get("/me")).json()

    assert me["id"] == str(USER_ID)  # String: JavaScript verfälscht 64-Bit-Zahlen
    assert me["username"] == "Jeremy"
    by_id = {g["id"]: g for g in me["guilds"]}
    assert set(by_id) == {str(GUILD_A), str(GUILD_C)}  # B: kein Recht
    assert by_id[str(GUILD_A)]["bot_present"] is True
    assert by_id[str(GUILD_C)]["bot_present"] is False  # Eigentümer, aber Bot fehlt


async def test_sitzungscookie_ist_httponly_lax_secure(api: Api) -> None:
    response = await api.client.get("/auth/login")
    state = httpx.URL(response.headers["location"]).params["state"]

    response = await api.client.get("/auth/callback", params={"code": "abc", "state": state})

    cookie = next(
        c for c in response.headers.get_list("set-cookie") if c.startswith(COOKIE_NAME)
    ).lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert "secure" in cookie
    assert "max-age=604800" in cookie
    assert response.headers["location"] == "https://bot.example.org/"


async def test_access_token_landet_weder_im_cookie_noch_in_der_antwort(api: Api) -> None:
    await login(api)

    assert "tok-123" not in (api.client.cookies.get(COOKIE_NAME) or "")
    assert "tok-123" not in (await api.client.get("/me")).text


async def test_callback_ohne_state_cookie_wird_abgelehnt(api: Api, discord: FakeDiscord) -> None:
    response = await api.client.get("/auth/callback", params={"code": "abc", "state": "x"})

    assert response.status_code == 400
    assert response.json()["detail"] == {"error": "oauth_state"}
    assert discord.calls == []  # Code wurde gar nicht erst eingelöst


async def test_callback_mit_fremdem_state_wird_abgelehnt(api: Api, discord: FakeDiscord) -> None:
    await api.client.get("/auth/login")

    response = await api.client.get(
        "/auth/callback", params={"code": "abc", "state": "vom-angreifer"}
    )

    assert response.status_code == 400
    assert discord.calls == []


async def test_callback_mit_gefaelschtem_state_cookie_wird_abgelehnt(api: Api) -> None:
    api.client.cookies.set(STATE_COOKIE, "angreifer.0000")

    response = await api.client.get("/auth/callback", params={"code": "abc", "state": "angreifer"})

    assert response.status_code == 400


async def test_abbruch_bei_discord_ohne_code_wird_abgelehnt(api: Api) -> None:
    response = await api.client.get("/auth/login")
    state = httpx.URL(response.headers["location"]).params["state"]

    response = await api.client.get(
        "/auth/callback", params={"error": "access_denied", "state": state}
    )

    assert response.status_code == 400


async def test_discord_lehnt_code_ab(api: Api, discord: FakeDiscord) -> None:
    discord.token_status = 400
    response = await api.client.get("/auth/login")
    state = httpx.URL(response.headers["location"]).params["state"]

    response = await api.client.get("/auth/callback", params={"code": "alt", "state": state})

    assert response.status_code == 502
    assert COOKIE_NAME not in response.headers.get("set-cookie", "")


async def test_state_ist_nur_einmal_nutzbar_pro_browser_cookie(api: Api) -> None:
    """Nach erfolgreichem Login ist das State-Cookie gelöscht."""
    await login(api)

    assert api.client.cookies.get(STATE_COOKIE) is None


async def test_me_ohne_cookie_ist_401(api: Api) -> None:
    response = await api.client.get("/me")

    assert response.status_code == 401
    assert response.json()["detail"] == {"error": "unauthenticated"}


async def test_manipuliertes_sitzungscookie_ist_401(api: Api) -> None:
    await login(api)
    sid, _, signature = api.client.cookies[COOKIE_NAME].rpartition(".")
    api.client.cookies.set(COOKIE_NAME, f"{sid}.{'0' * len(signature)}")

    assert (await api.client.get("/me")).status_code == 401


async def test_cookie_mit_fremder_signatur_ist_401(api: Api) -> None:
    await login(api)
    sid, _, _ = api.client.cookies[COOKIE_NAME].rpartition(".")
    api.client.cookies.set(COOKIE_NAME, sid)  # ohne Signatur

    assert (await api.client.get("/me")).status_code == 401


async def test_logout_loescht_sitzung_und_widerruft_token(api: Api, discord: FakeDiscord) -> None:
    await login(api)

    response = await api.client.post("/auth/logout", headers=CSRF)

    assert response.status_code == 204
    assert ("POST", "/api/v10/oauth2/token/revoke") in discord.calls
    assert (await api.client.get("/me")).status_code == 401


async def test_logout_gelingt_auch_wenn_widerruf_scheitert(api: Api, discord: FakeDiscord) -> None:
    await login(api)
    discord.revoke_status = 500

    response = await api.client.post("/auth/logout", headers=CSRF)

    assert response.status_code == 204
    assert (await api.client.get("/me")).status_code == 401


async def test_logout_braucht_csrf_header(api: Api) -> None:
    await login(api)

    response = await api.client.post("/auth/logout")

    assert response.status_code == 403
    assert response.json()["detail"] == {"error": "csrf"}
    assert (await api.client.get("/me")).status_code == 200  # Sitzung besteht weiter


async def test_ohne_geheimnisse_ist_login_aus_aber_gesundheit_ok(db: object, redis: object) -> None:
    from ggbot.api.main import create_app
    from ggbot.core.config import AppConfig
    from tests.conftest import TEST_DB_URL, TEST_REDIS_URL

    app = create_app(AppConfig(database_url=TEST_DB_URL, redis_url=TEST_REDIS_URL))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client,
    ):
        assert (await client.get("/auth/login")).status_code == 503
        assert (await client.get("/me")).status_code == 503
        assert (await client.get("/healthz")).status_code == 200


async def test_grosse_ids_gehen_als_string_raus(api: Api) -> None:
    """Über 2**53 verfälscht JavaScript JSON-Zahlen; deshalb Strings."""
    await login(api)

    snapshot = (await api.client.get(f"/guilds/{GUILD_A}/snapshot")).json()

    assert snapshot["guild_id"] == str(GUILD_A)
    assert snapshot["channels"][0]["id"] == str(BIG_CHANNEL)


async def test_me_liefert_daten_fuer_den_einladungslink(api: Api) -> None:
    await login(api)

    me = (await api.client.get("/me")).json()

    assert me["client_id"] == "1234"
    assert int(me["invite_permissions"]) == INVITE_PERMISSIONS


def test_einladungsrechte_entsprechen_discord_py() -> None:
    import discord

    expected = discord.Permissions(
        manage_channels=True,
        add_reactions=True,
        view_audit_log=True,
        view_channel=True,
        send_messages=True,
        manage_messages=True,
        embed_links=True,
        attach_files=True,
        read_message_history=True,
        manage_roles=True,
        manage_threads=True,
        kick_members=True,
        ban_members=True,
        moderate_members=True,
        use_application_commands=True,
    )
    assert expected.value == INVITE_PERMISSIONS


async def test_me_zeigt_ob_der_nutzer_rollen_panels_aendern_darf(
    api: Api, discord: FakeDiscord
) -> None:
    discord.guilds[0]["permissions"] = "8"  # A: Administrator
    discord.guilds[2]["owner"] = True  # C: Eigentümer ohne Rechte-Bits
    await login(api)

    by_id = {g["id"]: g for g in (await api.client.get("/me")).json()["guilds"]}

    assert by_id[str(GUILD_A)]["administrator"] is True
    assert by_id[str(GUILD_C)]["administrator"] is True


async def test_me_manage_server_ohne_administrator(api: Api) -> None:
    await login(api)

    by_id = {g["id"]: g for g in (await api.client.get("/me")).json()["guilds"]}

    assert by_id[str(GUILD_A)]["administrator"] is False
