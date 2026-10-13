import logging
import os

from aiohttp import web

from .server import make_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
web.run_app(make_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "8099")))
