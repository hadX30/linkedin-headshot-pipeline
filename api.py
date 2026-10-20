"""ASGI entry point:  uvicorn api:app --port 8000"""

from headshot.api.app import create_app

app = create_app()
