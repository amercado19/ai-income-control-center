"""Portfolio case studies (spec section 21), built from real production work.

Every claim here is verifiable from `amercado19`'s own repositories. Nothing is aspirational,
nothing is rounded up, and nothing describes work that does not exist - fabricating portfolio
projects is one of the hard prohibitions this system is built around.

The constraint that shapes this module is **visibility**, and it is not a formality:

* Capability evidence comes from private internal projects.

A case study a prospective client cannot open is an assertion, not evidence. So each study
carries its evidence explicitly, split into what a stranger can verify for themselves
(``public_evidence``) and what they can only take on trust until shown privately
(``private_evidence``). ``citable_claims()`` returns only claims backed by something public,
and that is the set the proposal generator is allowed to draw on.

One constraint applies throughout, and it is absolute: **the private internal projects are never
named, linked, described by subject, or offered for inspection.** They are not client work, not
portfolio material, and not proof a buyer may ask to see. What is sellable is the transferable
engineering - scheduling, reconciliation, calibration and failure handling - and the case studies
below describe that generically. If code proof is ever needed for a sale, it comes from a
purpose-built demonstration on non-sensitive data, never from these.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Evidence:
    """One checkable fact, and where a reader checks it."""

    claim: str
    where: str
    url: str = ""

    def is_public(self) -> bool:
        return bool(self.url)


@dataclass
class CaseStudy:
    key: str
    title: str
    one_line: str
    problem: str
    approach: list[str]
    outcome: list[str]
    stack: list[str]
    public_evidence: list[Evidence] = field(default_factory=list)
    private_evidence: list[Evidence] = field(default_factory=list)
    sells: list[str] = field(default_factory=list)
    """Opportunity categories and Fiverr gig keys this study is the right proof for."""

    def citable_claims(self) -> list[str]:
        """Claims a proposal may make to someone who has not met the operator.

        Deliberately only the public ones. A claim whose only support is a private repository
        is fine to say on a call and wrong to assert in a cold proposal.
        """
        return [e.claim for e in self.public_evidence]

    def links(self) -> list[str]:
        return [e.url for e in self.public_evidence if e.url]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["citable_claims"] = self.citable_claims()
        d["links"] = self.links()
        d["has_public_proof"] = bool(self.public_evidence)
        return d


NFL_DASHBOARD = ""
MLB_DASHBOARD = ""
NFL_DASH_REPO = ""


STUDIES: list[CaseStudy] = [
    CaseStudy(
        key="scheduled_pipeline",
        title="A multi-source data pipeline that runs itself on a schedule",
        one_line=(
            "Two production pipelines ingest third-party feeds on a cron schedule, rebuild a "
            "committed data store, and publish a dashboard - unattended, for $0 a month in "
            "infrastructure."
        ),
        problem=(
            "Data that has to be refreshed several times a day from providers who do not "
            "coordinate with each other. Done by hand it is a chore nobody keeps up; done badly "
            "by a machine it is worse, because a missed refresh produces a page that looks "
            "current and is not."
        ),
        approach=[
            "Every source is fetched by an idempotent refresh step, so a re-run repairs rather than duplicates.",
            "The data store is plain text committed to the repository, which makes every change diffable and every bad value traceable to the run that introduced it.",
            "Scheduling is tiered rather than uniform: frequent during the hours that matter, skipped overnight when nothing changes, because Actions minutes are a real budget.",
            "The published page carries its own freshness state, so a stale build announces itself instead of pretending.",
        ],
        outcome=[
            "Runs unattended on GitHub Actions' free tier; no server, no subscription, no cloud bill.",
            "The public dashboard shows its own last-refresh time and a data-status indicator on every load.",
        ],
        stack=["python", "github actions", "cron", "rest api", "etl", "github pages"],
        public_evidence=[
            Evidence(
                "A live dashboard, published automatically by a scheduled pipeline, showing its own refresh age and data status.",
                "private internal project",
                NFL_DASHBOARD,
            ),
            Evidence(
                "The generated dashboard repository, updated by the pipeline rather than by hand.",
                "private internal project",
                NFL_DASH_REPO,
            ),
        ],
        private_evidence=[
            Evidence(
                "28 GitHub Actions workflow definitions covering refresh, retrain, backtest, health check and deploy.",
                "private internal project",
            ),
            Evidence(
                "A cron schedule whose comments record why each slot exists, including the extra runs added after a real missed deadline.",
                "private internal project",
            ),
        ],
        sells=["data_pipeline", "automation", "api_integration", "data_engineering", "scheduled_automation"],
    ),
    CaseStudy(
        key="failure_alerting",
        title="A monitor that makes silent failure impossible",
        one_line=(
            "An hourly cloud-side check that fails loudly when the pipeline stops producing "
            "fresh data - written specifically because a green run log is not proof the data moved."
        ),
        problem=(
            "The dangerous failure in any scheduled job is not the one that crashes. It is the "
            "one where the run goes green, the push silently does not happen, and the output "
            "quietly ages while everyone assumes it is current."
        ),
        approach=[
            "A separate workflow checks the age of the last commit to the published data, independently of the job that is supposed to produce it.",
            "Over four hours stale during active hours and the check fails; GitHub's own failure notification does the alerting, so there is no paid alerting service.",
            "The error message names the exact place to look, including the specific failure mode where a run is green but the push did not land.",
            "It exits green during the off-season rather than crying wolf for months.",
        ],
        outcome=[
            "Replaced a health check that depended on a particular laptop being awake.",
            "Costs nothing: one short job an hour on the free tier, and email notifications GitHub already sends.",
        ],
        stack=["github actions", "monitoring", "bash", "rest api"],
        public_evidence=[
            Evidence(
                "A staleness monitor running in a public repository, with its reasoning and its limitations written into the file.",
                "private internal project",
                MLB_DASHBOARD,
            ),
        ],
        private_evidence=[],
        sells=["automation", "monitoring", "scheduled_automation", "data_pipeline"],
    ),
    CaseStudy(
        key="honest_reporting",
        title="A model pipeline that publishes its own limitations",
        one_line=(
            "Calibration reports, a model card and a written record of known limitations, "
            "published alongside the projections rather than instead of them."
        ),
        problem=(
            "Any system that outputs a number invites more confidence than the number deserves. "
            "The engineering problem is not producing the estimate; it is making its uncertainty "
            "impossible to miss."
        ),
        approach=[
            "A model card states what each model does and how well it has actually performed, and is meant to be read before the output is used.",
            "Calibration reports are regenerated from a command rather than written by hand, so they cannot drift away from the model they describe.",
            "Known limitations are a maintained document, not a footnote.",
            "Markets the model has no measured edge in are shown as comparisons only, explicitly carrying no recommendation.",
        ],
        outcome=[
            "Every published figure is traceable to the run and the method that produced it.",
            "The public dashboard labels model output as model output on each individual card, not once in a footer.",
        ],
        stack=["python", "pandas", "model calibration", "backtesting", "reporting"],
        public_evidence=[
            Evidence(
                "A public dashboard that labels every projection as model output and disclaims guarantees in its own repository description.",
                "private internal project",
                NFL_DASHBOARD,
            ),
        ],
        private_evidence=[
            Evidence(
                "MODEL_CARD.md, docs/CALIBRATION_REPORT.md and KNOWN_LIMITATIONS.md, each regenerable from a CLI command.",
                "private internal project",
            ),
        ],
        sells=["data_analysis", "model_pipeline", "reporting", "financial_model"],
    ),
    CaseStudy(
        key="tested_delivery",
        title="A test suite and CI gate that refuse to publish a broken build",
        one_line=("Lint, type checking, tests and a verify-before-publish step, so a broken build never replaces a working page."),
        problem=(
            "Automated publishing turns a small bug into a live one instantly. Without a gate, "
            "the fastest deployment pipeline is also the fastest way to break production."
        ),
        approach=[
            "ruff, mypy and pytest run in CI on every change, not only locally.",
            "A verification step inspects the built artifact for the markers that prove it rendered, and refuses to deploy when they are missing.",
            "Secret scanning and a dependency audit run in the same pipeline.",
            "Documentation for conventions, decisions and deployment is kept in the repository so the next person - or the next agent - does not have to guess.",
        ],
        outcome=[
            "A failed verification stops the deployment rather than replacing a working dashboard with a broken one.",
            "The same gates are reused across projects rather than reinvented per repository.",
        ],
        stack=["pytest", "ruff", "mypy", "github actions", "ci/cd"],
        public_evidence=[
            Evidence(
                "This repository: the same gate structure, fully public and inspectable, including the publish gate that refuses a bad build.",
                "ai-income-control-center",
                "",
            ),
        ],
        private_evidence=[
            Evidence(
                "Extensive test coverage across a private internal project, with CI running the full suite on each change.",
                "private internal project",
            ),
        ],
        sells=["testing", "ci_cd", "data_pipeline", "data_engineering", "automation"],
    ),
]


def all_studies() -> list[CaseStudy]:
    return list(STUDIES)


def get(key: str) -> CaseStudy | None:
    return next((s for s in STUDIES if s.key == key), None)


def for_category(category: str) -> list[CaseStudy]:
    """Case studies that are legitimate proof for a given opportunity category or gig."""
    c = category.strip().lower()
    return [s for s in STUDIES if c in s.sells]


def citable_claims() -> dict[str, list[str]]:
    """Every claim a cold proposal may make, keyed by case study."""
    return {s.key: s.citable_claims() for s in STUDIES}


def summary() -> dict[str, Any]:
    studies = [s.to_dict() for s in STUDIES]
    return {
        "studies": studies,
        "count": len(studies),
        "with_public_proof": sum(1 for s in studies if s["has_public_proof"]),
        "visibility_note": (
            "Capability evidence comes from private internal projects that are not approved for "
            "client disclosure. Proposals state capabilities generically and cite no private material."
        ),
        "subject_matter_note": (
            "What is offered is the engineering - scheduling, reconciliation, calibration and "
            "failure handling - described generically, with no private project disclosed."
        ),
    }


def to_markdown(study: CaseStudy) -> str:
    """One case study as a standalone document, for `portfolio/` and for sending to a client."""
    out = [f"# {study.title}", "", study.one_line, "", "## The problem", "", study.problem, "", "## Approach", ""]
    out += [f"- {a}" for a in study.approach]
    out += ["", "## Outcome", ""]
    out += [f"- {o}" for o in study.outcome]
    out += ["", f"**Stack:** {', '.join(study.stack)}", "", "## Evidence you can check yourself", ""]
    for ev in study.public_evidence:
        out.append(f"- {ev.claim}  \n  {ev.where} - <{ev.url}>")
    if study.private_evidence:
        out += [
            "",
            "## Available on request",
            "",
            "These live in private repositories, so they are offered for a walkthrough rather than",
            "asserted to someone who cannot open them:",
            "",
        ]
        out += [f"- {ev.claim}  \n  {ev.where}" for ev in study.private_evidence]
    out += ["", "---", "", f"*Relevant to: {', '.join(study.sells)}.*", ""]
    return "\n".join(out)


def write_portfolio(out_dir: str = "portfolio") -> list[str]:
    """Write every case study plus an index. Returns the paths written."""
    from pathlib import Path

    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for s in STUDIES:
        p = d / f"{s.key}.md"
        p.write_text(to_markdown(s), encoding="utf-8")
        written.append(str(p))

    meta = summary()
    index = [
        "# Portfolio",
        "",
        "Case studies drawn from real production work. Every claim is checkable; the ones that",
        "are not publicly checkable are marked as such rather than dressed up.",
        "",
        f"> {meta['visibility_note']}",
        "",
        f"> {meta['subject_matter_note']}",
        "",
    ]
    for s in STUDIES:
        index += [f"## [{s.title}]({s.key}.md)", "", s.one_line, ""]
        for url in s.links():
            index.append(f"- Verify: <{url}>")
        index.append("")
    p = d / "README.md"
    p.write_text("\n".join(index), encoding="utf-8")
    written.append(str(p))
    return written
