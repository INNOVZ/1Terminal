"""Compatibility entry point for deployments that previously served the trading API.

Both supported ASGI module paths now expose only the screener application.
"""
from algotrader.api.portal_app import app, create_app

__all__ = ["app", "create_app"]
