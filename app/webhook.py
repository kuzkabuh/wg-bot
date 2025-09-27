"""Webhook server for WireGuard Dashboard events.

WireGuard Dashboard (WGD) can send webhook notifications when the
configuration is changed.  This module spins up a small aiohttp server
to accept those webhooks and trigger a resynchronisation of the
database with the runtime.  Access is protected by a shared secret
specified in the configuration.
"""

from aiohttp import web
import json
import time

import app.context as ctx
from app.services.sync import resync_from_runtime


async def _wgd_handler(request: web.Request) -> web.Response:
    """Handle incoming webhook from WireGuard Dashboard.

    The webhook must include a ``secret`` query parameter matching
    ``WGD_WEBHOOK_SECRET`` in the environment; otherwise a 403 is
    returned.  The payload is logged for debugging purposes and the
    database is synchronised with the runtime state.
    """
    # Simple secret check in query (?secret=...)
    secret = request.query.get("secret", "")
    good_secret = getattr(ctx.SET, "WGD_WEBHOOK_SECRET", "")
    if good_secret and secret != good_secret:
        return web.Response(status=403, text="forbidden")

    try:
        payload = await request.json()
    except Exception:
        payload = None

    # Log and synchronise the database with the runtime
    await ctx.DBH.add_event(None, "info", f"WGD webhook: {json.dumps(payload)[:500] if payload else 'no-json'}")
    await resync_from_runtime()
    return web.json_response({"ok": True})


async def start_wgd_webhook() -> None:
    """Start the local HTTP server to accept WGD webhooks.

    The server is started only if ``WGD_WEBHOOK_ENABLED`` is true.  It
    listens on ``WGD_WEBHOOK_HOST:WGD_WEBHOOK_PORT`` and uses aiohttp.
    It logs a startup event via the database.
    """
    if not getattr(ctx.SET, "WGD_WEBHOOK_ENABLED", False):
        return
    host = getattr(ctx.SET, "WGD_WEBHOOK_HOST", "127.0.0.1")
    port = int(getattr(ctx.SET, "WGD_WEBHOOK_PORT", 8787))
    app = web.Application()
    app.router.add_post("/wgd", _wgd_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    await ctx.DBH.add_event(None, "info", f"WGD webhook server started on {host}:{port}")