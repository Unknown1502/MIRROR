"""MIRROR backend entrypoint: `uvicorn app.main:create_app --factory`."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import routes, ws
from .api.ratelimit import RateLimiter, client_ip
from .core.config import Settings, load_settings
from .core.container import build_container
from .core.errors import MirrorError
from .voice.relay import VoiceRegistry

log = logging.getLogger("mirror")

# blob: is needed for the AudioWorklet module; fonts come from Google Fonts.
def csp(frame_ancestors: str) -> str:
    return (
        "default-src 'self'; script-src 'self' blob:; worker-src 'self' blob:; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
        "connect-src 'self' ws: wss:; img-src 'self' data:; media-src 'self' blob:; "
        f"frame-ancestors {frame_ancestors}; base-uri 'none'; form-action 'self'"
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = FastAPI(title="MIRROR", version="1.0.0", description="Counterfactual runtime for voice agents")
    app.state.container = build_container(settings)
    app.state.voice = VoiceRegistry(app.state.container)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "X-Session-Id", "X-Demo-Token"],
    )

    @app.exception_handler(MirrorError)
    async def mirror_error(_: Request, exc: MirrorError):
        return JSONResponse(status_code=exc.http_status, content={"error": exc.to_dict()})

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        details = [{"loc": list(e.get("loc", [])), "msg": e.get("msg")} for e in exc.errors()]
        return JSONResponse(
            status_code=400, content={"error": {"code": "INVALID_REQUEST", "message": "Invalid request", "details": {"errors": details}}}
        )

    @app.exception_handler(Exception)
    async def unexpected(_: Request, exc: Exception):
        log.exception("unhandled error")
        return JSONResponse(status_code=500, content={"error": {"code": "INTERNAL", "message": "Internal error", "details": {}}})

    limiter = RateLimiter()
    policy = csp(settings.frame_ancestors)
    embeddable = settings.frame_ancestors != "'none'"

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        if settings.rate_limit:
            ip = client_ip(request.headers, request.client.host if request.client else None, settings.proxy_hops)
            ok, retry = limiter.check(request.method, request.url.path, ip, request.headers.get("x-session-id"))
            if not ok:
                return JSONResponse(
                    status_code=429,
                    headers={"Retry-After": str(int(retry) + 1)},
                    content={"error": {"code": "RATE_LIMITED", "message": "Too many requests. Slow down and retry shortly.",
                                       "details": {"retry_after_seconds": int(retry) + 1}}},
                )
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if not embeddable:
            response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Content-Security-Policy", policy)
        return response

    app.include_router(routes.router)
    app.include_router(ws.router)

    if settings.static_dir is not None:
        index = settings.static_dir / "index.html"
        app.mount("/assets", StaticFiles(directory=settings.static_dir / "assets"), name="assets")

        @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
        async def spa(path: str):
            if path == "api" or path.startswith("api/"):
                return JSONResponse(status_code=404, content={"error": {"code": "NOT_FOUND", "message": "No such API route", "details": {}}})
            candidate = (settings.static_dir / path).resolve()
            if path and candidate.is_file() and settings.static_dir.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(index)

    return app

