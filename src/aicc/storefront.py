"""The storefront ledger: what is actually live, and what it has actually produced.

Why this exists, and why it is small
------------------------------------
The gig kit records what is *ready*. `fiverr_status.json` records that a gig was marked ready.
Neither records that a gig went **live**, when, at what URL, or what happened next - and the moment
the first gig is published, "what happened next" is the only question that matters. There was
nowhere to put the answer, so the funnel would have been reconstructed from memory.

This is a ledger, not a dashboard and not an analytics engine. One row per live listing, the
figures a person can actually observe on the platform, and nothing computed that the observations
do not support.

The one rule it exists to enforce
---------------------------------
**Zero orders is not a zero conversion rate.** A rate needs a denominator with observations behind
it, and `analytics.MIN_OBSERVATIONS_FOR_RATE` already fixes how many this project considers
enough. Below that, and for any figure never observed, the value is `None` and every renderer
shows ``INSUFFICIENT DATA``. That is not a placeholder for a number we will get around to - it is
the honest value, and the distinction is the whole reason the project has an analytics module that
refuses to divide.

Nothing here performs a platform action. Publishing a gig is Andres's, by platform rule and by
policy; this module records that he did it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .analytics import MIN_OBSERVATIONS_FOR_RATE
from .config import DATA_DIR

#: One file, one row per live listing. Committed operational data: public facts about a public
#: storefront, no client material.
LEDGER_FILE = DATA_DIR / "storefront_ledger.json"

#: What every renderer shows for a figure with nothing behind it.
INSUFFICIENT = "INSUFFICIENT DATA"

#: Listing lifecycle. `HOLD` carries a reason, because a hold nobody can explain becomes a hold
#: nobody lifts - Gig 3 is held pending an outside-activity policy review, and that sentence has
#: to travel with the status.
STATES = ("READY_TO_PUBLISH", "LIVE", "HOLD", "PAUSED", "RETIRED")

#: Fiverr's package order, for rendering. Cheapest to dearest is how a buyer sees them and how
#: the three price/delivery/revision lists have to line up with each other.
TIERS = ("Basic", "Standard", "Premium")


@dataclass
class Listing:
    """One live (or held) storefront listing and its observed funnel.

    Every metric field defaults to ``None``, meaning *not observed*. That is deliberate and is
    not the same as zero: nobody has looked yet, or the platform has not reported it. Only
    `orders` and `gross_revenue` default to 0, because those are observable from the account the
    moment it exists - an order either happened or it did not.
    """

    platform: str
    gig_key: str
    service: str
    state: str = "READY_TO_PUBLISH"
    hold_reason: str = ""

    live_url: str = ""
    launched_at: str = ""

    package_prices: dict[str, float] = field(default_factory=dict)
    delivery_days: dict[str, int] = field(default_factory=dict)
    revisions: dict[str, int] = field(default_factory=dict)

    # Platform-reported. Fiverr shows impressions and clicks on a delay and only once a gig has
    # been live a while, so absent is the normal early state rather than an error.
    impressions: int | None = None
    clicks: int | None = None
    inquiries: int | None = None

    orders: int = 0
    gross_revenue: float = 0.0
    net_revenue: float = 0.0

    #: Minutes of Claude the kit estimates for this listing's tiers. An estimate, labelled.
    claude_estimate_minutes: int | None = None
    #: Minutes actually drawn, once a real order has been fulfilled and measured.
    claude_actual_minutes: int | None = None
    #: Andres's own minutes: publishing, buyer messages, review, delivery.
    andres_active_minutes: int = 0

    notes: str = ""
    updated_at: str = ""

    # -- derived, and honest about it -------------------------------------

    def conversion_rate(self) -> float | None:
        """Orders per click, as a percentage, or ``None`` when nothing supports it.

        Returns ``None`` - not 0.0 - when clicks were never observed or there are too few of them.
        `MIN_OBSERVATIONS_FOR_RATE` is shared with `analytics` so the two cannot drift into
        disagreeing about what counts as enough.
        """
        if self.clicks is None or self.clicks < MIN_OBSERVATIONS_FOR_RATE:
            return None
        return round(100 * self.orders / self.clicks, 1)

    def click_through_rate(self) -> float | None:
        if self.impressions is None or self.impressions < MIN_OBSERVATIONS_FOR_RATE:
            return None
        if self.clicks is None:
            return None
        return round(100 * self.clicks / self.impressions, 1)

    def net_per_claude_hour(self) -> float | None:
        """Net revenue per hour of Claude actually drawn. The efficiency figure that matters.

        Uses ACTUAL minutes only. Dividing real revenue by an estimate would produce a number
        that looks measured and is not.
        """
        if not self.claude_actual_minutes or self.net_revenue <= 0:
            return None
        return round(self.net_revenue / (self.claude_actual_minutes / 60), 2)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "platform": self.platform,
            "gig_key": self.gig_key,
            "service": self.service,
            "state": self.state,
            "hold_reason": self.hold_reason,
            "live_url": self.live_url,
            "launched_at": self.launched_at,
            "package_prices": self.package_prices,
            "delivery_days": self.delivery_days,
            "revisions": self.revisions,
            "impressions": self.impressions,
            "clicks": self.clicks,
            "inquiries": self.inquiries,
            "orders": self.orders,
            "gross_revenue": self.gross_revenue,
            "net_revenue": self.net_revenue,
            "claude_estimate_minutes": self.claude_estimate_minutes,
            "claude_actual_minutes": self.claude_actual_minutes,
            "andres_active_minutes": self.andres_active_minutes,
            "notes": self.notes,
            "updated_at": self.updated_at,
        }
        d["derived"] = {
            "conversion_rate_pct": self.conversion_rate(),
            "click_through_rate_pct": self.click_through_rate(),
            "net_per_claude_hour": self.net_per_claude_hour(),
            "note": (
                f"A rate needs at least {MIN_OBSERVATIONS_FOR_RATE} observations in its "
                f"denominator. Below that it is null and renders {INSUFFICIENT}; null is not zero."
            ),
        }
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Listing:
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


def load() -> list[Listing]:
    if not LEDGER_FILE.exists():
        return []
    try:
        raw = json.loads(LEDGER_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    rows = raw.get("listings", []) if isinstance(raw, dict) else raw
    return [Listing.from_dict(r) for r in rows if isinstance(r, dict)]


def save(listings: list[Listing]) -> None:
    LEDGER_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "listings": [x.to_dict() for x in listings],
        "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "min_observations_for_rate": MIN_OBSERVATIONS_FOR_RATE,
    }
    LEDGER_FILE.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def get(gig_key: str, *, platform: str = "fiverr") -> Listing | None:
    return next((x for x in load() if x.gig_key == gig_key and x.platform == platform), None)


def upsert(listing: Listing, *, actor: str) -> Listing:
    """Write one listing, recording who did it.

    ``actor`` is required rather than defaulted, for the same reason `fiverr_kit.mark_ready`
    requires it: a row saying ANDRES published a gig must only ever be written because Andres
    said he did.
    """
    from . import audit

    listing.updated_at = datetime.now(UTC).isoformat(timespec="seconds")
    rows = [x for x in load() if not (x.gig_key == listing.gig_key and x.platform == listing.platform)]
    rows.append(listing)
    rows.sort(key=lambda x: (x.platform, x.gig_key))
    save(rows)

    audit.record(
        "storefront.listing_recorded",
        actor=actor,
        object_type="listing",
        object_id=f"{listing.platform}:{listing.gig_key}",
        source=listing.platform,
        after={"state": listing.state, "live_url": listing.live_url, "orders": listing.orders},
    )
    return listing


def seed_from_kit(*, actor: str) -> list[Listing]:
    """Create a ledger row per gig in the kit, carrying its prices, terms and Claude estimate.

    Idempotent, and never overwrites an observed figure: a row that already exists keeps its
    state, URL, timestamp and every metric. Only the kit-derived fields are refreshed, so a
    price change in source shows up here without erasing what the platform reported.
    """
    from . import fiverr_kit as fk

    existing = {(x.platform, x.gig_key): x for x in load()}
    rows: list[Listing] = []
    for gig in fk.GIGS:
        if gig.bench:
            continue
        row = existing.get(("fiverr", gig.key)) or Listing(
            platform="fiverr",
            gig_key=gig.key,
            service=gig.title,
        )
        row.service = gig.title
        row.package_prices = {p.name: p.price for p in gig.packages}
        row.delivery_days = {p.name: p.delivery_days for p in gig.packages}
        row.revisions = {p.name: p.revisions for p in gig.packages}
        row.claude_estimate_minutes = int(round(sum(p.est_ai_hours for p in gig.packages) * 60))
        rows.append(row)

    for row in rows:
        upsert(row, actor=actor)
    return load()


def mark_live(gig_key: str, *, live_url: str, actor: str, launched_at: str = "", platform: str = "fiverr") -> tuple[bool, str]:
    """Record that a listing went live. Called only after Andres says it did.

    Refuses a row it has never seen and refuses an empty URL: a LIVE state with no address is a
    claim nobody can check, which is the class of green light this project exists to prevent.
    """
    row = get(gig_key, platform=platform)
    if row is None:
        return False, f"No ledger row for {platform}:{gig_key}. Run `aicc storefront seed` first."
    if not live_url.strip():
        return False, "Refused: a LIVE listing needs its URL. A live state with no address cannot be verified."

    row.state = "LIVE"
    row.live_url = live_url.strip()
    row.launched_at = launched_at or datetime.now(UTC).isoformat(timespec="seconds")
    row.hold_reason = ""
    upsert(row, actor=actor)
    return True, f"LIVE {platform}:{gig_key} at {row.live_url} ({row.launched_at})"


def hold(gig_key: str, *, reason: str, actor: str, platform: str = "fiverr") -> tuple[bool, str]:
    """Put a listing on hold with a reason that travels with it.

    The reason is mandatory. A hold whose cause is not written down is indistinguishable later
    from an oversight, and the thing most likely to happen to it is being quietly published.
    """
    if not reason.strip():
        return False, "Refused: a hold needs a reason. An unexplained hold is one nobody knows how to lift."
    row = get(gig_key, platform=platform)
    if row is None:
        return False, f"No ledger row for {platform}:{gig_key}. Run `aicc storefront seed` first."
    if row.state == "LIVE":
        return False, f"Refused: {gig_key} is already LIVE. Pause it on the platform first, then record PAUSED."

    row.state = "HOLD"
    row.hold_reason = reason.strip()
    upsert(row, actor=actor)
    return True, f"HOLD {platform}:{gig_key} - {row.hold_reason}"


def observe(
    gig_key: str,
    *,
    actor: str,
    platform: str = "fiverr",
    impressions: int | None = None,
    clicks: int | None = None,
    inquiries: int | None = None,
    orders: int | None = None,
    gross_revenue: float | None = None,
    net_revenue: float | None = None,
    claude_actual_minutes: int | None = None,
    andres_active_minutes: int | None = None,
) -> tuple[bool, str]:
    """Record observed platform figures. Only what is passed is changed.

    Every argument defaults to ``None`` meaning *not reported this time*, so recording an
    impression count never silently zeroes an order count.
    """
    row = get(gig_key, platform=platform)
    if row is None:
        return False, f"No ledger row for {platform}:{gig_key}."

    for name, value in (
        ("impressions", impressions),
        ("clicks", clicks),
        ("inquiries", inquiries),
        ("orders", orders),
        ("gross_revenue", gross_revenue),
        ("net_revenue", net_revenue),
        ("claude_actual_minutes", claude_actual_minutes),
        ("andres_active_minutes", andres_active_minutes),
    ):
        if value is not None:
            setattr(row, name, value)

    upsert(row, actor=actor)
    return True, f"Recorded for {platform}:{gig_key}."


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _fmt(value: Any, *, money: bool = False, pct: bool = False) -> str:
    if value is None:
        return INSUFFICIENT
    if money:
        return f"${value:,.2f}"
    if pct:
        return f"{value}%"
    return str(value)


def summary() -> dict[str, Any]:
    """Portfolio totals. Revenue sums are real; every rate can be INSUFFICIENT DATA."""
    rows = load()
    live = [x for x in rows if x.state == "LIVE"]

    # Money sums EVERY row; only the funnel rate is restricted to what is currently live.
    #
    # These were both computed over `live`, and that is wrong in a way that would have bitten
    # exactly once, at the worst moment. `spreadsheet_cleanup` is priced below the floor
    # deliberately and the plan is to RETIRE it at Level 1 - so the first orders arrive on a gig
    # that is then taken down, and retiring it would have erased its revenue from the total and
    # flipped FIRST $100 NET from REACHED back to NOT YET. Money earned does not un-earn when a
    # listing is paused. A conversion rate over retired listings, by contrast, really is
    # meaningless, so that one keeps the filter.
    total_orders = sum(x.orders for x in rows)
    total_gross = round(sum(x.gross_revenue for x in rows), 2)
    total_net = round(sum(x.net_revenue for x in rows), 2)
    live_clicks = sum(x.clicks or 0 for x in live)
    live_orders = sum(x.orders for x in live)

    return {
        "listings": len(rows),
        "live": len(live),
        "held": [{"gig": x.gig_key, "reason": x.hold_reason} for x in rows if x.state == "HOLD"],
        "orders": total_orders,
        "gross_revenue": total_gross,
        "net_revenue": total_net,
        "portfolio_conversion_pct": (round(100 * live_orders / live_clicks, 1) if live_clicks >= MIN_OBSERVATIONS_FOR_RATE else None),
        "first_100_net_reached": total_net >= 100.00,
        "andres_active_minutes": sum(x.andres_active_minutes for x in rows),
    }


def format_report() -> str:
    rows = load()
    s = summary()
    lines = [
        "STOREFRONT LEDGER",
        "",
        f"  LISTINGS               {s['listings']} ({s['live']} live)",
        f"  ORDERS                 {s['orders']}",
        f"  GROSS REVENUE          {_fmt(s['gross_revenue'], money=True)}",
        f"  NET REVENUE            {_fmt(s['net_revenue'], money=True)}",
        f"  PORTFOLIO CONVERSION   {_fmt(s['portfolio_conversion_pct'], pct=True)}",
        f"  FIRST $100 NET         {'REACHED' if s['first_100_net_reached'] else 'NOT YET'}",
        f"  ANDRES ACTIVE MINUTES  {s['andres_active_minutes']}",
        "",
    ]
    if not rows:
        lines.append("  No listings recorded. Run `python -m aicc storefront seed`.")
        return "\n".join(lines)

    for x in rows:
        lines.append(f"  [{x.state}] {x.platform}:{x.gig_key}")
        lines.append(f"        {x.service}")
        if x.hold_reason:
            lines.append(f"        HOLD: {x.hold_reason}")
        if x.live_url:
            lines.append(f"        {x.live_url}  (live since {x.launched_at})")
        # Rendered in tier order, not dict order. The store writes with sort_keys=True, so the
        # keys come back Basic/Premium/Standard - and "$95 / $625 / $250" alongside
        # "5 / 16 / 9d" reads as though Standard costs $625. A ledger that is misread is worse
        # than one that is unread.
        prices = " / ".join(f"${x.package_prices[t]:,.0f}" for t in TIERS if t in x.package_prices) or INSUFFICIENT
        days = " / ".join(str(x.delivery_days[t]) for t in TIERS if t in x.delivery_days) or INSUFFICIENT
        revs = " / ".join(str(x.revisions[t]) for t in TIERS if t in x.revisions) or INSUFFICIENT
        lines.append(f"        prices {prices}   delivery {days}d   revisions {revs}")
        lines.append(
            f"        impressions {_fmt(x.impressions)}   clicks {_fmt(x.clicks)}   inquiries {_fmt(x.inquiries)}   orders {x.orders}"
        )
        lines.append(f"        conversion {_fmt(x.conversion_rate(), pct=True)}   CTR {_fmt(x.click_through_rate(), pct=True)}")
        lines.append(
            f"        gross {_fmt(x.gross_revenue, money=True)}   net {_fmt(x.net_revenue, money=True)}   "
            f"net/Claude-hour {_fmt(x.net_per_claude_hour(), money=True)}"
        )
        est = f"{x.claude_estimate_minutes}m (ESTIMATED)" if x.claude_estimate_minutes else INSUFFICIENT
        act = f"{x.claude_actual_minutes}m" if x.claude_actual_minutes else INSUFFICIENT
        lines.append(f"        Claude est {est}   actual {act}   Andres {x.andres_active_minutes}m")
        lines.append("")

    lines.append(f"  A rate stays {INSUFFICIENT} until at least {MIN_OBSERVATIONS_FOR_RATE} observations back it.")
    lines.append("  Zero orders is not a zero conversion rate.")
    return "\n".join(lines)
