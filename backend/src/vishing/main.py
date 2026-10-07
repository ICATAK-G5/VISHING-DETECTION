from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from vishing.api.connection import DesktopStore, router
from vishing.core.config import Settings, private_ipv4_addresses, settings
from vishing.core.security import ensure_certificate, make_tls_context


def load_settings() -> Settings:
    config_path = settings.data_dir / "connection-settings.json"
    allow_lan = False
    if config_path.exists():
        try:
            allow_lan = bool(json.loads(config_path.read_text(encoding="utf-8")).get("allow_lan", False))
        except (OSError, ValueError):
            allow_lan = False
    return Settings(http_port=settings.http_port, secure_port=settings.secure_port, allow_lan=allow_lan)


runtime_settings = load_settings()
fingerprint = ensure_certificate(runtime_settings)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.settings = runtime_settings
    app.state.allow_lan = runtime_settings.allow_lan
    if not hasattr(app.state, "store"):
        app.state.store = DesktopStore(runtime_settings, fingerprint)
    yield


app = FastAPI(title="Vishing Detection Desktop", version="0.1.0", docs_url=None, redoc_url=None, lifespan=lifespan)
app.include_router(router)
app.mount("/assets", StaticFiles(directory=runtime_settings.dashboard_dir / "assets"), name="assets")


@app.middleware("http")
async def restrict_desktop_controls_to_local_machine(request: Request, call_next):
    if request.url.path == "/" or request.url.path.startswith("/api/v1/desktop/"):
        client = request.client.host if request.client else ""
        import ipaddress
        try:
            local = ipaddress.ip_address(client).is_loopback
        except ValueError:
            local = False
        if not local:
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": "Desktop controls are available only on this computer."}, status_code=403)
    response = await call_next(request)
    if request.url.path == "/":
        response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.get("/")
def dashboard():
    return FileResponse(runtime_settings.dashboard_dir / "index.html")


async def serve() -> None:
    lan_addresses = private_ipv4_addresses()
    if runtime_settings.allow_lan and not lan_addresses:
        raise RuntimeError("Local network access is enabled, but no private IPv4 address is available. Connect to Wi-Fi and restart the service.")
    secure_hosts = lan_addresses if runtime_settings.allow_lan else ["127.0.0.1"]
    local = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=runtime_settings.http_port, log_level="info", lifespan="off"))
    secure_servers = []
    for host in secure_hosts:
        secure_config = uvicorn.Config(
            app, host=host, port=runtime_settings.secure_port, log_level="info", lifespan="off"
        )
        secure_config.load()
        secure_config.ssl = make_tls_context(runtime_settings)
        secure_servers.append(uvicorn.Server(secure_config))
    async with app.router.lifespan_context(app):
        servers = [local, *secure_servers]
        tasks = [asyncio.create_task(server.serve()) for server in servers]
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for server in servers:
            server.should_exit = True
        await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(serve())
