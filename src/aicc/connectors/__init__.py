"""Marketplace and feed connectors.

Importing this package registers every connector. ``registry()`` then returns them by name.
"""

# Import for side-effect registration. Order controls dashboard display order.
from . import (
    contra,  # noqa: F401,E402
    demo,  # noqa: F401,E402
    feeds,  # noqa: F401,E402
    fiverr,  # noqa: F401,E402
    freelancer_com,  # noqa: F401,E402
    hackernews,  # noqa: F401,E402
    python_jobs,  # noqa: F401,E402
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

LIVE_DISCOVERY_ORDER = [
    "hackernews",  # highest-value: real contract rates, no reputation gate
    "freelancer_com",  # the only open project marketplace; filtered hard on competition
    "python_jobs",  # small but every listing is on-stack
    "himalayas",
    "remoteok",
    "weworkremotely",
]
"""Sources that can legitimately be scanned unattended, in priority order.

Upwork, Contra and Fiverr are deliberately absent, and for three different reasons:

* **Upwork** prohibits automated discovery outright, and its API & MCP Terms additionally forbid
  enumerating the corpus and cap caching at 24 hours.
* **Contra** has no opportunity discovery to offer a freelancer at all - its MCP is a back office
  (proposals, invoices, portfolio) for clients you already have.
* **Fiverr** has no discovery surface in any form; it is a storefront.

Also absent, and deliberately: the HN "Freelancer? Seeking freelancer?" thread (measured at 4
demand posts against 191 supply posts over nine months), and Jobicy, Arbeitnow and Working Nomads,
all of which have clean licences and working APIs but almost no genuine freelance work.
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
