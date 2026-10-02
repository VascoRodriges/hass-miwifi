"""Luci API client exceptions."""


class LuciError(Exception):
    """Luci error"""


class LuciConnectionError(LuciError):
    """Luci connection error"""


class LuciRequestError(LuciError):
    """Luci request error"""


class LuciWriteUncertainError(LuciConnectionError):
    """The router may have applied a write whose response was lost.

    Read back the state; never retry the command automatically.
    """
