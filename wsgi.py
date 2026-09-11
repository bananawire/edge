"""WSGI entry point for a production server (single process).

    uv run waitress-serve --listen=0.0.0.0:5000 --threads=8 wsgi:app

Workers are started once at import, before the first request, so readiness is meaningful.
"""

from app import app, start_workers

start_workers()
