"""Sicherheitsköpfe und Zugriffsprotokoll der API."""

import time

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import RequestResponseEndpoint

from ggbot.core.logging import get_logger

log = get_logger("ggbot.api.access")

HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def secure(request: Request, call_next: RequestResponseEndpoint) -> Response:
        started = time.perf_counter()
        response = await call_next(request)
        for name, value in HEADERS.items():
            response.headers.setdefault(name, value)
        # Antworten gehören einem angemeldeten Nutzer (Einstellungen, Protokoll, Server-Liste) und
        # dürfen weder im Browser-Verlauf noch in einem Proxy liegen bleiben. Wer es besser weiss
        # (öffentliche Rangliste, Vorschaubild), setzt den Kopf selbst.
        response.headers.setdefault("Cache-Control", "no-store")
        # Nur der Pfad, nie die Query: im Rücksprung von Discord steht der einmalige Anmelde-Code.
        log.info(
            "request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            ms=round((time.perf_counter() - started) * 1000, 1),
        )
        return response
