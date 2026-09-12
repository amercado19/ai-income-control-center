"""The Fiverr Launch Wizard: the exact sequence for publishing four gigs by hand, safely.

Fiverr has no seller API. Publishing is therefore a person sitting at a form, and the risk is
not that the gig is wrong - ``fiverr_kit`` already validates every platform constraint - but
that a **locked-in** field is filled wrongly in a moment of momentum. Fiverr locks two things
the instant you save: the gig's category, and its URL slug (derived from the first title saved).
Neither can be changed afterwards. A gig in the wrong category is not a gig with a small problem;
it is a gig nobody will ever find, and the only fix is to delete it and burn one of the four
slots a new seller gets.

So this module exists to do one thing: put the irreversible decisions in front of Andres BEFORE
the momentum starts, in the order the form asks for them, with the exact value to paste into
each field. Everything reversible is marked reversible, so he knows where he can move fast.

It deliberately does not automate anything. Automating a Fiverr form would mean driving a UI the
platform has not sanctioned for it, and the whole storefront depends on that account staying in
good standing. The wizard's value is in being right about the order and honest about what locks.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from . import fiverr_kit
from .fiverr_kit import Gig


@dataclass
class Field:
    """One thing to fill in, and whether getting it wrong is recoverable."""

    label: str
    value: str
    locked_after_save: bool = False
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Step:
    number: int
    title: str
    fields: list[Field] = field(default_factory=list)
    instruction: str = ""
    irreversible: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "title": self.title,
            "instruction": self.instruction,
            "irreversible": self.irreversible,
            "fields": [f.to_dict() for f in self.fields],
        }


#: Fiverr's gig creation flow, in the order the form presents it. Keeping the wizard's order
#: identical to the form's matters more than it sounds: a checklist in a different order than
#: the screen is a checklist people stop following halfway down.
def steps_for(gig: Gig) -> list[Step]:
    from .fiverr_kit import capacity_outlook

    pkgs = gig.packages
    _cap = {row["package"]: row for row in capacity_outlook(gig)}
    return [
        Step(
            1,
            "Overview",
            irreversible=True,
            instruction=(
                "Two fields on this screen lock permanently the moment you press Save. Read both "
                "values before typing anything. If either looks wrong, stop here - it is free to "
                "fix now and impossible to fix later."
            ),
            fields=[
                Field(
                    "Gig title",
                    gig.title,
                    locked_after_save=True,
                    note=f"{len(gig.title)}/80 characters. The URL slug is generated from this and never changes, "
                    f"even if you edit the title afterwards.",
                ),
                Field(
                    "Category",
                    gig.category,
                    locked_after_save=True,
                    note="CANNOT be changed after saving. A gig in the wrong category is invisible; the only "
                    "remedy is deleting it, which costs one of your four slots.",
                ),
                Field(
                    "Subcategory",
                    gig.subcategory,
                    locked_after_save=True,
                    note="Also locked. Chosen for search volume, not for how it reads.",
                ),
                Field("Search tags", ", ".join(gig.tags), note=f"{len(gig.tags)}/5. Editable later, so this one is safe to iterate on."),
            ],
        ),
        Step(
            2,
            "Pricing - three packages",
            instruction=(
                "Prices are list prices. Fiverr takes 20%, so the net column is what actually "
                "arrives. Delivery days are calendar days the buyer sees, not hours of work - "
                "they are deliberately longer than the effort estimate so a bad week does not "
                "become a late delivery. Each tier also carries its Claude demand, checked "
                "against its own delivery promise: a tier that does not fit is a promise to "
                "reconsider before the category locks, not after a buyer is waiting."
            ),
            fields=[
                Field(
                    f"{p.name} - ${p.price:.0f}",
                    f"${p.price:.0f} list → ${p.net:.2f} net · {p.delivery_days} day delivery · "
                    f"{p.revisions} revision(s) · ~{p.est_human_hours:.2f}h of your time"
                    + (f" · ${p.implied_hourly:.0f}/h implied" if p.implied_hourly else ""),
                    note="Includes: " + "; ".join(p.includes),
                )
                for p in pkgs
            ]
            + [
                Field(
                    f"{p.name} - Claude workload",
                    f"~{_cap[p.name]['claude_minutes']:.0f} min of Claude, "
                    f"{_cap[p.name]['total_demand_minutes']:.0f} min with QA and one revision, "
                    f"against {p.delivery_days} days: {_cap[p.name]['status']}"
                    + ("" if _cap[p.name]["fits_delivery_window"] else "  <-- DOES NOT FIT THIS DELIVERY WINDOW"),
                    note=(
                        "Checked by the same pre-job capacity check a real order goes through."
                        if _cap[p.name]["fits_delivery_window"]
                        else "Reconsider this tier's delivery window or its scope BEFORE publishing - after a buyer orders is too late."
                    ),
                )
                for p in pkgs
                if p.name in _cap
            ],
        ),
        Step(
            3,
            "Description and FAQ",
            instruction="Both are editable after publishing. Paste, then read once for anything that reads as a promise you cannot keep.",
            fields=[
                Field("Description", gig.description, note=f"{len(gig.description)}/1200 characters."),
                *[Field(f"FAQ - {f['q']}", f["a"]) for f in gig.faqs],
            ],
        ),
        Step(
            4,
            "Requirements",
            instruction=(
                "The order clock does not start until the buyer answers these. An incomplete "
                "questionnaire is the most common cause of a late delivery that was never the "
                "seller's fault - every question here exists to prevent one."
            ),
            fields=[Field(f"Question {i + 1}", q) for i, q in enumerate(gig.requirements)],
        ),
        Step(
            5,
            "Gallery",
            instruction=(
                "Up to three images at 1280x769. The rendered image is in portfolio/gig_images/. "
                "Regenerate with: python3 scripts/gig_images.py"
            ),
            fields=[
                Field("Image", gig.image_path() or "NOT YET RENDERED - run scripts/gig_images.py", note=gig.image_concept),
            ],
        ),
        Step(
            6,
            "Publish",
            irreversible=True,
            instruction=(
                "Last check before the category locks. Confirm the category on screen matches "
                "step 1 exactly, then publish. After this the gig is live and buyers can order it."
            ),
            fields=[
                Field("Confirm category", f"{gig.category} > {gig.subcategory}", locked_after_save=True),
                Field("Confirm title", gig.title, locked_after_save=True),
            ],
        ),
    ]


#: The order to publish in, and why. Not arbitrary: the first gig published is the one Fiverr's
#: new-seller boost is spent on, so it should be the one with the best category economics rather
#: than the one that is easiest to fill in.
PUBLISH_ORDER = [
    (
        "data_engineering",
        "First. The thinnest technical category measured (~1,500 gigs) with the "
        "highest price floors, and the closest match to two production pipelines. "
        "The new-seller impression boost is worth most here.",
    ),
    ("scheduled_automation", "Second. Highest demand density measured - about 5.7 category reviews per gig. Good odds of an early order."),
    (
        "financial_model",
        "Third. Real differentiation (MBA in financial technologies, research-grants finance) in a category of generic modellers.",
    ),
    (
        "spreadsheet_cleanup",
        "Fourth, and deliberately last. This one is priced below the floor to "
        "buy the first reviews. Publish it knowing that, and retire it at Level 1.",
    ),
]


def plan() -> dict[str, Any]:
    """The whole wizard: what to publish, in what order, with every locked field surfaced."""
    by_key = {g.key: g for g in fiverr_kit.all_gigs()}
    ordered: list[dict[str, Any]] = []

    for position, (key, why) in enumerate(PUBLISH_ORDER, start=1):
        gig = by_key.get(key)
        if gig is None:
            continue
        problems = gig.validate()
        gig_steps = steps_for(gig)
        ordered.append(
            {
                "position": position,
                "key": gig.key,
                "title": gig.title,
                "status": gig.status,
                "ready": not problems,
                "blocking": problems,
                "why_this_order": why,
                "below_floor_reason": gig.below_floor_reason,
                "image_ready": bool(gig.image_path()),
                "locked_fields": [
                    {"label": f.label, "value": f.value, "note": f.note} for s in gig_steps for f in s.fields if f.locked_after_save
                ],
                "steps": [s.to_dict() for s in gig_steps],
                "estimated_minutes": 12,
            }
        )

    bench = [{"key": g.key, "title": g.title, "rationale": g.rationale} for g in fiverr_kit.all_gigs() if g.bench]

    ready = [g for g in ordered if g["ready"]]
    return {
        "gigs": ordered,
        "bench": bench,
        "ready_count": len(ready),
        "total_slots": fiverr_kit.NEW_SELLER_GIG_SLOTS,
        "all_ready": len(ready) == len(ordered) and len(ordered) == fiverr_kit.NEW_SELLER_GIG_SLOTS,
        "estimated_total_minutes": sum(g["estimated_minutes"] for g in ordered),
        "irreversible_count": sum(len(g["locked_fields"]) for g in ordered),
        "human_required": True,
        "human_required_reason": (
            "Fiverr has no seller API, and driving the seller UI with automation is not something "
            "the platform sanctions. The storefront is the revenue path, so the account staying in "
            "good standing outranks the convenience of automating a twelve-minute form."
        ),
        "what_claude_did": (
            "Researched the categories, wrote and priced every gig, validated each against every "
            "platform limit, rendered the images, and ordered the publishing sequence. Everything "
            "except the twelve minutes of typing."
        ),
        "warning": (
            "Category and title-derived URL lock permanently on save. There are "
            f"{sum(len(g['locked_fields']) for g in ordered)} such fields across the four gigs, and "
            "each is flagged in its step. Read those before typing."
        ),
    }


def render(key: str | None = None) -> str:
    """The wizard as plain text, for the terminal."""
    p = plan()
    lines: list[str] = []
    lines.append("FIVERR LAUNCH WIZARD")
    lines.append(
        f"  {p['ready_count']} of {p['total_slots']} gigs ready to publish · "
        f"~{p['estimated_total_minutes']} minutes total · "
        f"{p['irreversible_count']} fields that lock permanently"
    )
    lines.append("")
    lines.append(f"  {p['warning']}")
    lines.append("")

    for g in p["gigs"]:
        if key and g["key"] != key:
            continue
        mark = "READY" if g["ready"] else "BLOCKED"
        lines.append(f"{'=' * 78}")
        lines.append(f"{g['position']}. [{mark}] {g['title']}")
        lines.append(f"   {g['why_this_order']}")
        if g["below_floor_reason"]:
            lines.append(f"   BELOW FLOOR, DELIBERATELY: {g['below_floor_reason'][:160]}")
        if g["blocking"]:
            for b in g["blocking"]:
                lines.append(f"   BLOCKING: {b}")
        if not g["image_ready"]:
            lines.append("   NOTE: gig image not rendered. Run: python3 scripts/gig_images.py")
        lines.append("")
        for s in g["steps"]:
            flag = "  <-- IRREVERSIBLE" if s["irreversible"] else ""
            lines.append(f"   Step {s['number']}: {s['title']}{flag}")
            if s["instruction"]:
                lines.append(f"      {s['instruction']}")
            for f in s["fields"]:
                lock = " [LOCKS ON SAVE]" if f["locked_after_save"] else ""
                value = f["value"] if len(f["value"]) <= 150 else f["value"][:147] + "..."
                lines.append(f"      - {f['label']}{lock}")
                lines.append(f"          {value}")
                if f["note"]:
                    lines.append(f"          ({f['note'][:150]})")
            lines.append("")

    if p["bench"]:
        lines.append(f"{'=' * 78}")
        lines.append("BENCH - written, priced and imaged, ready to swap in:")
        for b in p["bench"]:
            lines.append(f"   {b['title']}")
    lines.append("")
    lines.append(f"  {p['what_claude_did']}")
    lines.append(f"  {p['human_required_reason']}")
    return "\n".join(lines)
