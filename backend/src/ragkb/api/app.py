"""Stable public facade for the modular FastAPI application."""

from ragkb.api.application import OPENAPI_VERSION, create_app

__all__ = ["OPENAPI_VERSION", "create_app"]
