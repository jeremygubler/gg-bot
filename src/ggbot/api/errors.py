"""Übersetzt `UserError` aus den Diensten in eine 422-Antwort in der Sprache der Oberfläche."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ggbot.core.errors import UserError
from ggbot.core.i18n import LOCALES, translator


def request_locale(request: Request, default: str) -> str:
    wanted = request.headers.get("x-locale", "")
    return wanted if wanted in LOCALES else default


def install(app: FastAPI, default_locale: str) -> None:
    @app.exception_handler(UserError)
    async def user_error(request: Request, exc: UserError) -> JSONResponse:
        message = translator.t(request_locale(request, default_locale), exc.key, **exc.params)
        return JSONResponse(
            status_code=422, content={"detail": {"error": "user_error", "message": message}}
        )
