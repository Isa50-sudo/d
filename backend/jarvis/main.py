"""JARVIS – Einstiegspunkt des lokalen Backends.

Start:  python -m jarvis.main      (aus dem Ordner backend/)
        oder start.bat / start.sh
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from jarvis.api import routes, ws
from jarvis.config.env import get_env
from jarvis.core.logging_setup import setup_logging
from jarvis.core.paths import FRONTEND_DIR, ensure_directories
from jarvis.core.security import LocalOnlyMiddleware
from jarvis.services.container import Services

log = logging.getLogger("jarvis")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    services: Services = app.state.services
    await services.start()
    services.events.add("system", "JARVIS gestartet", "success")
    ok, message = await services.ai.check(services.settings.current.ai.model)
    services.events.add("ai", f"Ollama: {message}", "success" if ok else "warning")
    try:
        yield
    finally:
        services.events.add("system", "JARVIS wird beendet", "info")
        await services.stop()


def create_app() -> FastAPI:
    ensure_directories()
    env = get_env()
    services = Services(env)
    setup_logging(services.settings.current.logging.level)

    app = FastAPI(title="JARVIS", version="1.0.0", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.services = services
    app.add_middleware(LocalOnlyMiddleware, port=env.jarvis_port)

    app.include_router(routes.router)
    app.include_router(ws.router)

    @app.exception_handler(Exception)
    async def _unhandled(_request, exc: Exception) -> JSONResponse:  # type: ignore[no-untyped-def]
        log.exception("Unbehandelter Fehler: %s", exc)
        return JSONResponse({"detail": "Interner Fehler – Details stehen im Entwickler-Log."}, status_code=500)

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(FRONTEND_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/", StaticFiles(directory=FRONTEND_DIR), name="frontend")
    return app


def run() -> None:
    env = get_env()
    if env.jarvis_host not in ("127.0.0.1", "localhost", "::1"):
        print("WARNUNG: JARVIS_HOST ist nicht lokal – das Backend wäre im Netzwerk erreichbar!")
    print(f"\n  JARVIS läuft auf  http://127.0.0.1:{env.jarvis_port}\n")
    uvicorn.run(create_app(), host=env.jarvis_host, port=env.jarvis_port, log_level="warning", ws_max_size=1 << 20)


if __name__ == "__main__":
    run()
