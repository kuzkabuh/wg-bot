"""Telegram bot handlers package.

This package groups all handler definitions for the bot.  It exposes
convenience imports so callers can register both user and admin
handlers without touching individual modules.  The functions
`register_user_handlers` and `register_admin_handlers` can be
imported directly from this package for convenience.

The actual implementations live in the sibling modules `user` and
`admin`.
"""

from .user import register_user_handlers  # noqa: F401
from .admin import register_admin_handlers  # noqa: F401

__all__ = [
    "register_user_handlers",
    "register_admin_handlers",
]