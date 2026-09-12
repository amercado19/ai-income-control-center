"""Fiverr launch kit: platform limits, pricing arithmetic, and honesty constraints.

These tests exist because Fiverr locks several of these decisions permanently at publish time.
The category cannot be changed after publishing and the gig URL is fixed from the first saved
title, so a constraint violation that reaches the platform is not a bug you fix - it is a gig
slot you burn, out of the four a new seller gets.
"""

from __future__ import annotations

import pytest

from aicc import fiverr_kit as fk
from aicc.config import PROFILE

ALL = list(fk.GIGS)  # source definitions, before any persisted status overlay


# ------------------------------------------------------------------ platform limits


def test_every_gig_satisfies_every_fiverr_constraint() -> None:
    problems = fk.validate_all()
    assert problems == {g.key: [] for g in ALL}, problems


@pytest.mark.parametrize("gig", ALL, ids=lambda g: g.key)
def test_title_fits_and_uses_fiverrs_required_opening(gig: fk.Gig) -> None:
    assert gig.title.startswith("I will")
    assert len(gig.title) <= fk.MAX_TITLE_CHARS


@pytest.mark.parametrize("gig", ALL, ids=lambda g: g.key)
def test_description_fits(gig: fk.Gig) -> None:
    assert len(gig.description) <= fk.MAX_DESCRIPTION_CHARS


@pytest.mark.parametrize("gig", ALL, ids=lambda g: g.key)
def test_tags_within_limit_and_lowercase(gig: fk.Gig) -> None:
    # Fiverr lowercases tags itself; storing them lowercased keeps the preview honest.
    assert len(gig.tags) <= fk.MAX_TAGS
    assert gig.tags == [t.lower() for t in gig.tags]


@pytest.mark.parametrize("gig", ALL, ids=lambda g: g.key)
def test_three_packages_each_with_a_revision(gig: fk.Gig) -> None:
    assert [p.name for p in gig.packages] == ["Basic", "Standard", "Premium"]
    assert all(p.revisions >= 1 for p in gig.packages)


def test_live_gigs_fit_the_slots_a_new_seller_has() -> None:
    """Five candidates, four slots - which is not a contradiction, and treating it as one was
    the earlier mistake. Four go live; the fifth is written, priced and imaged, waiting to
    replace whichever gig gets no impressions."""
    live = [g for g in ALL if not g.bench]
    assert len(live) == fk.NEW_SELLER_GIG_SLOTS
    assert len(ALL) > len(live), "A bench candidate should exist so a dud can be swapped quickly."


def test_a_bench_gig_is_as_finished_as_a_live_one() -> None:
    """A bench candidate that still needs work is not a bench candidate, it is a TODO."""
    for gig in [g for g in ALL if g.bench]:
        assert gig.validate() == [], gig.validate()
        assert gig.image_path(), f"{gig.key} has no rendered image, so swapping it in is not one step."
        assert len(gig.faqs) >= 4 and len(gig.requirements) >= 3


def test_the_summary_counts_only_live_gigs_against_the_slot_limit() -> None:
    s = fk.summary()
    assert s["slots_used"] == fk.NEW_SELLER_GIG_SLOTS
    assert s["bench"], "The bench must be visible, or it will be forgotten."


def test_gig_keys_are_unique() -> None:
    keys = [g.key for g in ALL]
    assert len(keys) == len(set(keys))


# ------------------------------------------------------------------ pricing arithmetic


def test_list_price_grosses_up_for_the_commission() -> None:
    assert fk.list_price_for_net(100.0) == 125.0
    assert fk.list_price_for_net(300.0) == 375.0


def test_net_is_the_inverse_of_the_gross_up() -> None:
    for net in (24.0, 60.0, 100.0, 500.0):
        assert fk.Package("x", fk.list_price_for_net(net), 1, 1).net == pytest.approx(net)


@pytest.mark.parametrize("gig", ALL, ids=lambda g: g.key)
def test_packages_are_monotonic_in_price_delivery_and_revisions(gig: fk.Gig) -> None:
    prices = [p.price for p in gig.packages]
    days = [p.delivery_days for p in gig.packages]
    revs = [p.revisions for p in gig.packages]
    assert prices == sorted(prices) and len(set(prices)) == 3
    assert days == sorted(days)
    assert revs == sorted(revs)


@pytest.mark.parametrize("gig", ALL, ids=lambda g: g.key)
def test_higher_tiers_deliver_more_than_just_a_bigger_number(gig: fk.Gig) -> None:
    sizes = [len(p.includes) for p in gig.packages]
    assert sizes[-1] >= sizes[0], "Premium must include strictly more than Basic, not cost more."


@pytest.mark.parametrize("gig", ALL, ids=lambda g: g.key)
def test_every_package_declares_an_effort_estimate(gig: fk.Gig) -> None:
    """Without one, the hourly return is unknowable and the floor check is theatre."""
    for pkg in gig.packages:
        assert pkg.est_human_hours > 0, f"{gig.key}/{pkg.name} declares no operator hours."
        assert pkg.est_ai_hours >= pkg.est_human_hours, (
            f"{gig.key}/{pkg.name} claims AI does less work than the human. If that is true the "
            "gig is mispriced for this business; if it is not true the estimate is wrong."
        )


@pytest.mark.parametrize("gig", ALL, ids=lambda g: g.key)
def test_gig_clears_the_operator_floor_or_says_in_writing_why_not(gig: fk.Gig) -> None:
    """A gig that cannot clear the floor is a gig that loses money politely.

    Underpricing is allowed - review-gated ranking makes it rational at the start - but only as
    a declared decision. Silence is the failure mode this catches.
    """
    thin = [p.name for p in gig.packages if (p.implied_hourly or 0) < PROFILE.minimum_hourly]
    if thin:
        assert gig.below_floor_reason, f"{gig.key} prices {thin} under ${PROFILE.minimum_hourly:.0f}/h with no declared reason."


@pytest.mark.parametrize("gig", ALL, ids=lambda g: g.key)
def test_higher_tiers_do_not_pay_worse_per_hour(gig: fk.Gig) -> None:
    """The scope-creep trap: buyer pays more, seller earns less per hour for the privilege."""
    hourlies = [p.implied_hourly or 0 for p in gig.packages]
    assert min(hourlies[1:]) >= hourlies[0] * 0.9, f"{gig.key} hourly returns degrade up the tiers: {hourlies}"


def test_exactly_one_gig_is_below_floor_and_it_is_the_review_harvester() -> None:
    """If this starts failing, the kit has drifted from 'one loss leader' to 'cheap across the board'."""
    below = [g.key for g in ALL if g.below_floor_reason]
    assert below == ["spreadsheet_cleanup"], below


def test_the_below_floor_exemption_names_its_own_exit() -> None:
    """An exemption with no retirement condition is just a permanent discount with paperwork."""
    reason = next(g.below_floor_reason for g in ALL if g.below_floor_reason) or ""
    assert "Level 1" in reason
    assert any(w in reason.upper() for w in ("RETIRE", "RAISE"))
    assert fk.summary()["below_floor"][0]["key"] == "spreadsheet_cleanup"


def test_summary_reports_commission_adjusted_totals() -> None:
    s = fk.summary()
    assert s["commission"] == fk.COMMISSION
    assert s["all_valid"] is True
    assert s["total_basic_net"] == pytest.approx(sum(g.packages[0].net for g in ALL))


# ------------------------------------------------------------------ honesty + compliance


@pytest.mark.parametrize("gig", ALL, ids=lambda g: g.key)
def test_no_gig_starts_published(gig: fk.Gig) -> None:
    """Spec: 'Do NOT publish gigs without my approval.'

    DRAFT is the only legal state in SOURCE. READY_TO_PUBLISH is legitimate operational state
    that `aicc fiverr ready` sets after validation - it means "checked and queued for Andres",
    not "live". PUBLISHED is the one value nothing in this system may ever write, because only
    a person sitting at fiverr.com can make that true.
    """
    assert gig.status == "DRAFT"


def test_summary_states_that_publishing_is_manual() -> None:
    note = fk.summary()["publishing_note"].lower()
    assert "not published automatically" in note or "no seller api" in note


@pytest.mark.parametrize("gig", ALL, ids=lambda g: g.key)
def test_descriptions_make_no_claim_the_operator_cannot_back(gig: fk.Gig) -> None:
    """Fabricated experience is a hard prohibition, and Fiverr suspends accounts over it.

    Superlatives are the tell. A gig description may say what the work includes; it may not
    assert a track record that does not exist yet - there are no Fiverr reviews on day one.
    """
    banned = [
        "years of experience",
        "hundreds of clients",
        "thousands of",
        "5-star",
        "five star",
        "award-winning",
        "certified expert",
        "guaranteed satisfaction",
        "100% satisfaction",
        "best on fiverr",
        "top rated",
        "trusted by",
    ]
    lowered = gig.description.lower()
    found = [phrase for phrase in banned if phrase in lowered]
    assert not found, f"{gig.key} claims {found}, which is not demonstrable."


@pytest.mark.parametrize("gig", ALL, ids=lambda g: g.key)
def test_every_gig_has_a_requirements_questionnaire(gig: fk.Gig) -> None:
    # An incomplete questionnaire stops the order clock, which is the single most common way a
    # new seller's on-time-delivery rate gets destroyed by something that was not their fault.
    assert len(gig.requirements) >= 3


@pytest.mark.parametrize("gig", ALL, ids=lambda g: g.key)
def test_every_gig_documents_why_it_earned_a_slot(gig: fk.Gig) -> None:
    assert len(gig.rationale) > 80
    assert len(gig.image_concept) > 40


def test_ai_disclosure_note_is_present_and_does_not_advise_concealment() -> None:
    note = fk.summary()["ai_disclosure_note"].lower()
    assert "disclos" in note
    for evasive in ("do not mention", "avoid mentioning", "conceal", "hide the"):
        assert evasive not in note


def test_to_dict_round_trips_the_fields_the_dashboard_renders() -> None:
    d = ALL[0].to_dict()
    for key in ("key", "title", "packages", "faqs", "requirements", "valid", "title_chars", "status"):
        assert key in d
    assert d["packages"][0]["net_after_commission"] == ALL[0].packages[0].net


# ------------------------------------------------------------------ validator actually bites


def test_validator_catches_an_over_length_title() -> None:
    bad = fk.Gig(
        key="t",
        title="I will " + "x" * fk.MAX_TITLE_CHARS,
        category="Data",
        subcategory="x",
        tags=["a"],
        description="d",
        packages=[fk.Package("Basic", 10.0, 1, 1), fk.Package("Standard", 20.0, 2, 1), fk.Package("Premium", 30.0, 3, 1)],
        faqs=[],
        requirements=["q"],
        image_concept="i",
        rationale="r",
    )
    assert any("Title is" in p for p in bad.validate())


def test_validator_catches_a_missing_revision_and_a_sub_minimum_price() -> None:
    bad = fk.Gig(
        key="t",
        title="I will do a thing",
        category="Data",
        subcategory="x",
        tags=["a"],
        description="d",
        packages=[fk.Package("Basic", 3.0, 1, 0), fk.Package("Standard", 20.0, 2, 1), fk.Package("Premium", 30.0, 3, 1)],
        faqs=[],
        requirements=["q"],
        image_concept="i",
        rationale="r",
    )
    problems = bad.validate()
    assert any("revisions" in p for p in problems)
    assert any("minimum" in p for p in problems)


def test_validator_catches_characters_fiverr_rejects_in_titles() -> None:
    bad = fk.Gig(
        key="t",
        title="I will clean & consolidate your data",
        category="Data",
        subcategory="x",
        tags=["a"],
        description="d",
        packages=[fk.Package("Basic", 10.0, 1, 1), fk.Package("Standard", 20.0, 2, 1), fk.Package("Premium", 30.0, 3, 1)],
        faqs=[],
        requirements=["q"],
        image_concept="i",
        rationale="r",
    )
    assert any("'&'" in p for p in bad.validate())


# ------------------------------------------------------------------ gig images


def test_image_path_is_empty_rather_than_broken_when_nothing_is_rendered(tmp_path, monkeypatch) -> None:
    """A missing image is a real gap the dashboard should show, not a 404 it should hide."""
    monkeypatch.chdir(tmp_path)
    assert ALL[0].image_path() == ""


def test_summary_counts_how_many_gigs_have_an_image() -> None:
    """Counted across all candidates, not only the live four - the bench needs its image too,
    or swapping it in is two jobs instead of one."""
    s = fk.summary()
    assert 0 <= s["images_ready"] <= len(ALL)


def test_every_gig_has_an_image_renderer() -> None:
    """A gig with no renderer would publish without an image, which performs badly on Fiverr.

    Reads the registry out of the source rather than importing it. `pytest` runs from its own
    managed environment, which has no Pillow, so importing the script would make this check
    SKIP - and a skipped check is not a passing one. The renderer registry is a plain literal,
    so the text is as reliable here as the object would be, and it works everywhere.
    """
    import re
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "scripts" / "gig_images.py").read_text(encoding="utf-8")
    block = re.search(r"^RENDERERS\s*[:=].*?\{(.*?)^\}", source, re.S | re.M)
    assert block, "RENDERERS registry not found in scripts/gig_images.py"
    registered = set(re.findall(r'"([a-z_]+)":', block.group(1)))
    missing = {g.key for g in ALL} - registered
    assert not missing, f"No image renderer for {sorted(missing)} - those gigs would publish without one."


def test_nothing_in_this_system_can_mark_a_gig_published() -> None:
    """The state that would be a lie if the system set it. Only fiverr.com makes a gig live."""
    import inspect

    source = inspect.getsource(fk)
    assert '"PUBLISHED"' not in source.replace("# DRAFT | READY_TO_PUBLISH | PUBLISHED", ""), (
        "Something in fiverr_kit assigns PUBLISHED. Only a person at fiverr.com can make that true."
    )
    for gig in fk.all_gigs():
        assert gig.status != "PUBLISHED"


# ------------------------------------------------- the launch wizard (amendment: priority 16)


def test_every_irreversible_field_is_flagged_before_it_locks() -> None:
    """Category and the title-derived URL lock permanently on save. A gig in the wrong category
    is invisible, and the only remedy costs one of four new-seller slots. Surfacing those fields
    late is the same as not surfacing them."""
    from aicc import fiverr_wizard

    plan = fiverr_wizard.plan()
    assert plan["irreversible_count"] >= 8, plan["irreversible_count"]
    for gig in plan["gigs"]:
        labels = {f["label"] for f in gig["locked_fields"]}
        assert any("Category" in lbl for lbl in labels), gig["key"]
        assert any("title" in lbl.lower() for lbl in labels), gig["key"]
        # The first step a person sees must be the one carrying the locks.
        assert gig["steps"][0]["irreversible"], gig["key"]


def test_the_wizard_covers_exactly_the_four_slots_and_never_the_bench() -> None:
    from aicc import fiverr_kit, fiverr_wizard

    plan = fiverr_wizard.plan()
    assert len(plan["gigs"]) == fiverr_kit.NEW_SELLER_GIG_SLOTS
    bench_keys = {g.key for g in fiverr_kit.all_gigs() if g.bench}
    assert not ({g["key"] for g in plan["gigs"]} & bench_keys)
    assert plan["bench"], "The fifth candidate should still be described as bench."


def test_the_publish_order_is_justified_not_arbitrary() -> None:
    from aicc import fiverr_wizard

    for gig in fiverr_wizard.plan()["gigs"]:
        assert gig["why_this_order"], gig["key"]
        assert len(gig["why_this_order"]) > 40, gig["why_this_order"]


def test_the_wizard_says_plainly_that_publishing_is_a_human_step() -> None:
    from aicc import fiverr_wizard

    plan = fiverr_wizard.plan()
    assert plan["human_required"] is True
    assert "no seller API" in plan["human_required_reason"]


def test_every_step_carries_an_instruction_or_at_least_one_field() -> None:
    from aicc import fiverr_wizard

    for gig in fiverr_wizard.plan()["gigs"]:
        for step in gig["steps"]:
            assert step["fields"] or step["instruction"], (gig["key"], step["title"])


# ------------------------------------------------- status is persisted, not only audited


def test_marking_a_gig_ready_survives_into_what_the_dashboard_reads() -> None:
    """An earlier version wrote an audit event and nothing else, so the dashboard went on
    reporting DRAFT for a gig that had been marked ready. The log knew; the screen did not."""
    from aicc import fiverr_kit

    ok, msg = fiverr_kit.mark_ready("data_engineering", actor="CLAUDE")
    assert ok, msg
    gig = next(g for g in fiverr_kit.all_gigs() if g.key == "data_engineering")
    assert gig.status == "READY_TO_PUBLISH"
    assert fiverr_kit.summary()["ready_to_publish"] >= 1


def test_marking_ready_records_who_actually_did_it() -> None:
    from aicc import audit, fiverr_kit

    fiverr_kit.mark_ready("financial_model", actor="CLAUDE")
    events = [e for e in audit.read_all(50) if e.action == "fiverr.mark_ready"]
    assert events
    assert events[0].actor == "CLAUDE"


def test_an_invalid_gig_cannot_be_marked_ready() -> None:
    from aicc import fiverr_kit

    ok, msg = fiverr_kit.mark_ready("no_such_gig", actor="CLAUDE")
    assert not ok
    assert "No gig with key" in msg


# ------------------------------------------- what each tier costs in Claude, not just in hours


def test_every_published_tier_can_be_delivered_within_its_own_promise() -> None:
    """The gap this closes.

    The kit priced every tier against Andres's hours and said nothing about the resource the rest
    of the system treats as scarce. A gig could therefore promise a five-day turnaround on work
    whose AI demand does not fit five days of windows, and nothing would have said so until a real
    buyer was already waiting - which is the one moment when the answer cannot be changed.
    """
    from aicc import fiverr_kit

    for gig in fiverr_kit.all_gigs():
        for row in fiverr_kit.capacity_outlook(gig):
            assert row["fits_delivery_window"], (
                f"{gig.key} {row['package']} promises {row['delivery_days']} days but needs "
                f"{row['total_demand_minutes']:.0f} min of Claude: {row['reason']}"
            )


def test_the_capacity_outlook_reports_real_claude_minutes_not_a_placeholder() -> None:
    from aicc import fiverr_kit

    for gig in fiverr_kit.all_gigs():
        rows = fiverr_kit.capacity_outlook(gig)
        assert len(rows) == len(gig.packages)
        for row, pkg in zip(rows, gig.packages, strict=True):
            assert row["claude_minutes"] == pytest.approx(pkg.est_ai_hours * 60.0)
            # Demand is never the bare worker pass: QA, one revision and a margin are included.
            assert row["total_demand_minutes"] > row["claude_minutes"]


def test_a_tier_that_cannot_be_delivered_on_time_is_caught_before_publishing() -> None:
    """A gate that cannot fail is not a gate. Forty hours of AI work promised in one day is the
    shape of the mistake this exists to catch - and it must read as unfit, not merely tight."""
    from dataclasses import replace

    from aicc import fiverr_kit

    gig = fiverr_kit.all_gigs()[0]
    impossible = replace(gig, packages=[replace(gig.packages[0], name="Impossible", est_ai_hours=40.0, delivery_days=1)])
    row = fiverr_kit.capacity_outlook(impossible)[0]

    assert not row["fits_delivery_window"]
    assert row["status"] == "RISKY"
    assert "exceeds" in row["reason"]


def test_the_summary_carries_the_outlook_so_the_dashboard_can_show_it() -> None:
    from aicc import fiverr_kit

    for gig in fiverr_kit.summary()["gigs"]:
        assert gig["capacity_outlook"], f"{gig['key']} has no capacity outlook"
        assert {r["package"] for r in gig["capacity_outlook"]} == {p["name"] for p in gig["packages"]}
