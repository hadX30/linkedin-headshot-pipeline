"""ASGI entry point: uvicorn api:app"""

from headshot.api.app import create_app

app = create_app()
