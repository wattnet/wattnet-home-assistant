"""Schema library used by Home Assistant.

Recent Home Assistant releases validate with ``probatio``; older ones with
``voluptuous``. Both expose the same API for what this integration uses, so
the one the running Home Assistant ships is picked here.
"""

from __future__ import annotations

try:
    import probatio as vol
except ImportError:  # Home Assistant older than the switch to probatio
    import voluptuous as vol  # type: ignore[no-redef]

__all__ = ["vol"]
