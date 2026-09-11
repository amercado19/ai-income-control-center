"""Marketplace and feed connectors.

Importing this package registers every connector. ``registry()`` then returns them by name.
"""

# Import for side-effect registration. Order controls dashboard display order.
from . import (
    contra,  # noqa: F401,E402
    demo,  # noqa: F401,E402
    feeds,  # noqa: F401,E402
    fiverr,  # noqa: F401,E402
    hackernews,  # noqa: F401,E402
    upwork,  # noqa: F401,E402
)
from .base import (  # noqa: F401
    Capabilities,
    Connector,
    ConnectorError,
    NotPermittedError,
    get,
    registry,
)

LIVE_DISCOVERY_ORDER = ["hackernews", "himalayas", "remoteok", "weworkremotely"]
"""Sources that can legitimately be scanned unattended, in priority order.

Upwork, Contra and Fiverr are deliberately absent: none of them has a permitted unattended
discovery surface. That is a fact about those platforms, not a gap in this system.
"""

__all__ = [
    "Capabilities",
    "Connector",
    "ConnectorError",
    "NotPermittedError",
    "get",
    "registry",
    "LIVE_DISCOVERY_ORDER",
]
