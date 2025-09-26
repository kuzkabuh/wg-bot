from aiohttp import web
import json
import time

import app.context as ctx
from app.services.sync import resync_from_runtime

async def _wgd_handler(request: web.Request):
    # Простая защита по секрету в query (?secret=...)
    secret = request.query.get("secret", "")
    good_secret = getattr(ctx.SET, "WGD_WEBHOOK_SECRET", "")
    if good_secret and secret != good_secret:
        return web.Response(status=403, text="forbidden")

    try:
        payload = await request.json()
    except Exception:
        payload = None

    # Логируем и синхронизируем БД с рантаймом wg
    await ctx.DBH.add_event(None, "info", f"WGD webhook: {json.dumps(payload)[:500] if payload else 'no-json'}")
    await resync_from_runtime()
    return web.json_response({"ok": True})

async def start_wgd_webhook():
    """
    Поднимаем мини-HTTP сервер для приёма вебхуков WGDashboard.
    Работает параллельно с polling Telegram.
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
