"""Run as python -m scripts.guru_preview. Separate local salon, bound only to loopback."""
import argparse
from pathlib import Path
import secrets
import tempfile

from aiohttp import web

from games.guru_webapp import handle, serve_guru
from infrastructure.persistence.guru_miniapp import GuruVisits


def create_app(database, *, free_shop=False):
    store = GuruVisits(database, free_shop=free_shop)

    async def page(request):
        response = await serve_guru(request)
        if not request.cookies.get("guru_demo"):
            response.set_cookie("guru_demo", str(secrets.randbits(61)), httponly=True, samesite="Strict",
                                max_age=365 * 86400)
        return response

    async def api(request):
        if request.headers.get("Origin") not in (None, f"http://{request.host}"):
            raise web.HTTPForbidden()
        raw = request.cookies.get("guru_demo", "")
        if not raw.isdigit() or len(raw) > 19:
            raise web.HTTPUnauthorized()
        return await handle(request, int(raw), store, link="")

    app = web.Application()
    app.router.add_get("/game", page)
    app.router.add_post("/game", api)
    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8785)
    parser.add_argument("--db", type=Path, default=Path(tempfile.gettempdir()) / "upupa-guru-preview.db")
    parser.add_argument("--free-shop", action="store_true", help="Buy props for 0 ₽ in the local preview.")
    args = parser.parse_args()
    web.run_app(create_app(args.db, free_shop=args.free_shop), host="127.0.0.1", port=args.port)
