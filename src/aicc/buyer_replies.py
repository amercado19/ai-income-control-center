"""Reusable replies for a buyer conversation, before there is a buyer.

Why these are written down
--------------------------
The first inquiry arrives while Andres is doing something else, and the reply that matters most
is the fastest one. Fiverr's ranking also weighs response time. Writing them now costs nothing;
composing them under time pressure is how a seller ends up promising a date they cannot keep, or
answering a scope question with "sure, no problem".

Every template is a draft, never a send. Nothing in this module contacts anybody. There is no
Fiverr messaging API, and even if there were, a message to a paying client is a commitment.

What every template must survive
--------------------------------
* **No off-platform contact.** Fiverr's terms prohibit moving a buyer to email, WhatsApp or
  anywhere else, and doing it once can cost the account. The account is the revenue path.
* **No fabricated experience.** Only claims the verified profile backs - two production
  pipelines, scheduled GitHub Actions jobs, research-grants finance, an MBA in financial
  technologies. Never a client name, never a duration nobody recorded.
* **No guarantees.** "Guaranteed", "100%", "risk-free" are unverifiable and Fiverr-flagged.
* **No credential requests.** Read-only exports or a scoped key; never an account password.
* **A scope answer says what is NOT included.** A yes that omits the boundary is how scope creep
  starts, and the revision the buyer then expects is free work.

A test asserts the first four of those against every template in this file.

On the injection template specifically
--------------------------------------
Client-supplied text - a brief, a README inside an uploaded zip, a cell in a spreadsheet - is
untrusted input, and this project already treats it that way everywhere else (`untrusted.py`, a
51-case regression suite). A buyer file that contains instructions addressed to an AI is not
necessarily an attack; it is sometimes a README that happens to read like one. Either way the
answer is the same: the instructions in a file are data, and they do not get executed. The
template says so plainly, without accusing the buyer of anything.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Reply:
    """One situation and the draft for it."""

    key: str
    situation: str
    when: str
    body: str
    note: str = ""


REPLIES: tuple[Reply, ...] = (
    Reply(
        key="first_inquiry",
        situation="First buyer inquiry",
        when="Someone messages before ordering. Answer within a few hours if you can; Fiverr weighs response time.",
        body=(
            "Thanks for reaching out.\n\n"
            "Tell me three things and I can give you a straight answer on fit:\n\n"
            "1. What the data looks like now - a sample file or a screenshot is ideal, anonymised is fine\n"
            "2. What you need out the other end\n"
            "3. When you need it by\n\n"
            "If it is a good fit I will tell you which package covers it. If it is not, I will tell "
            "you that too rather than take the order and find out later."
        ),
    ),
    Reply(
        key="scope_clarification",
        situation="Scope clarification",
        when="The buyer asks whether something specific is included.",
        body=(
            "Short answer: yes, that is in scope for the {package} package.\n\n"
            "What that covers: {included}\n\n"
            "What it does not cover: {excluded}. If you need that as well, it fits better in "
            "{alternative} - happy to send a custom offer instead of having you buy the wrong tier."
        ),
        note="Fill the four braces. The 'does not cover' line is the point of the message; a yes without a boundary is where scope creep starts.",
    ),
    Reply(
        key="requirements_collection",
        situation="Requirements collection",
        when="The order is placed but the questionnaire is thin or blank.",
        body=(
            "Thanks for the order. Before the clock starts I need a few things so I build the "
            "right thing first time:\n\n"
            "{questions}\n\n"
            "Send whatever you have and I will tell you if anything is still missing. Sample data "
            "is usually enough to start - I do not need access to your live systems."
        ),
        note="Paste the five questionnaire questions for that gig. Fiverr's clock does not start until the buyer answers, so asking is free.",
    ),
    Reply(
        key="insufficient_requirements",
        situation="Insufficient requirements after asking",
        when="You asked, and what came back still is not workable.",
        body=(
            "I want to get this right, so I would rather ask again than guess.\n\n"
            "What I still need: {missing}\n\n"
            "Specifically, without {missing_short} I would be making a decision that is really "
            "yours to make - and you would find out at delivery instead of now.\n\n"
            "If it is easier, send the raw file and I will tell you what I see in it."
        ),
    ),
    Reply(
        key="polite_decline",
        situation="Polite decline of bad-fit work",
        when="The request is legitimate but outside what you do well.",
        body=(
            "Thanks for thinking of me. I am going to pass on this one.\n\n"
            "It needs {what_it_needs}, and that is outside what I do well - you would get a better "
            "result from someone who works on that specifically. I would rather say so now than "
            "deliver something adequate.\n\n"
            "If you have anything involving {what_you_do}, I would be glad to look."
        ),
        note="Declining well is worth more than a mediocre first review. A 3-star delivery on a bad-fit job costs more than the order was worth.",
    ),
    Reply(
        key="pricing_mismatch",
        situation="Pricing or scope mismatch",
        when="The buyer wants Premium work at the Basic price, or asks for a discount.",
        body=(
            "I can do that - it is a {actual_tier} job rather than {requested_tier}, because "
            "{reason}.\n\n"
            "Two options:\n\n"
            "1. {actual_tier} at ${actual_price} and you get the whole thing\n"
            "2. Trim it to {trimmed_scope}, which genuinely fits {requested_tier}\n\n"
            "Either works for me. What I will not do is quote the lower price and then ask for "
            "more later."
        ),
        note="Never discount the first order to win it. A below-floor precedent follows the account, and spreadsheet_cleanup is already the deliberate loss-leader.",
    ),
    Reply(
        key="delivery_date",
        situation="Delivery date question",
        when="The buyer asks when it will be ready, or wants it faster.",
        body=(
            "The {package} package is {days} days, and that is a calendar window rather than how "
            "long the work takes - it is deliberately longer so a bad week does not become a late "
            "delivery.\n\n"
            "Realistically you will have it before then. If you have a hard date, tell me what it "
            "is and I will tell you honestly whether I can hit it before you order."
        ),
    ),
    Reply(
        key="revision_request",
        situation="Revision request",
        when="The buyer asks for changes after delivery.",
        body=(
            "Thanks - that is exactly what the revisions are for. Sending an updated version.\n\n"
            "To be sure I fix the right thing: {clarify}\n\n"
            "If what you are after is {new_scope}, that is a different job rather than a revision - "
            "I would rather flag that than quietly do it and have the boundary be unclear next time."
        ),
        note="Distinguish a revision (the thing you agreed, done right) from new scope (a different thing). Doing new scope free once sets the expectation.",
    ),
    Reply(
        key="scope_creep",
        situation="Scope creep mid-order",
        when="The work keeps growing after the order started.",
        body=(
            "Happy to do that - flagging it because it is beyond what we agreed, and I would rather "
            "say so now than at delivery.\n\n"
            "In scope and on track: {agreed}\n"
            "New: {additional}\n\n"
            "I can send a custom offer for the extra, or park it and deliver what we agreed first. "
            "Your call - both are fine."
        ),
    ),
    Reply(
        key="ai_disclosure",
        situation="AI disclosure",
        when="The buyer asks whether you use AI, or says they want no AI involved. Fiverr requires an explicit no-AI request to be honoured.",
        body=(
            "Fair question. I use AI tooling as part of how I work, the same way I use a test suite "
            "or a linter - I write the approach, it accelerates the work, and I review and test "
            "everything before it reaches you. The code is documented and yours to keep.\n\n"
            "If you need work produced without AI involvement, say so and I will either do it that "
            "way or tell you it is not a fit. I will not say no and then use it anyway."
        ),
        note="If the buyer asks for non-AI work, that request is binding under Fiverr's terms. Honour it or decline the order - and note it on the order so a later session knows.",
    ),
    Reply(
        key="client_files",
        situation="Client-provided files",
        when="The buyer sends data, especially anything that looks personal or confidential.",
        body=(
            "Got the files, thanks.\n\n"
            "How I handle them: I work only with what you send, I do not share it, and I delete it "
            "on request once the order is complete. If any of it is personal or regulated data, an "
            "anonymised sample is usually enough for me to build against - I would rather have less "
            "of your data than more.\n\n"
            "I also do not need account passwords. A read-only export or a scoped key is enough for "
            "almost everything."
        ),
        note="Client material goes in workspaces/, which is gitignored and never committed. Check that before working on anything real.",
    ),
    Reply(
        key="prohibited_request",
        situation="Suspicious or prohibited request",
        when="Scraping behind a login, bypassing a platform's terms, anything presented as urgent and off-platform, or work that is someone's coursework.",
        body=(
            "I am not able to take this on.\n\n"
            "{reason} - and that is a line I hold regardless of the budget, because the account and "
            "the work both depend on it.\n\n"
            "If there is a version of this that stays inside {platform_or_rule}, I am glad to look "
            "at that instead."
        ),
        note=(
            "Do not negotiate and do not explain at length. If it involves payment off Fiverr, a "
            "request to communicate elsewhere, or anything that reads like account takeover, report "
            "it to Fiverr rather than only declining."
        ),
    ),
    Reply(
        key="injection_attempt",
        situation="Instructions to an AI inside a buyer file",
        when=(
            "An uploaded file, README or spreadsheet cell contains text addressed to an AI - "
            "'ignore previous instructions', 'you are now...', a hidden prompt. Often not an "
            "attack; sometimes a README that reads like one."
        ),
        body=(
            "Quick note on the files you sent - one of them contains text that reads as "
            "instructions to an automated tool ({where}).\n\n"
            "I have treated it as content rather than as instructions, which is the right handling "
            "either way, so nothing was acted on. Flagging it in case it is left over from "
            "something else and you did not know it was in there.\n\n"
            "Carrying on with the brief as agreed."
        ),
        note=(
            "Never accuse. The honest technical fact is the whole message: text in a file is data. "
            "The tripwire in untrusted.py already refuses to act on it; this tells the buyer so."
        ),
    ),
)


def get(key: str) -> Reply | None:
    return next((r for r in REPLIES if r.key == key), None)


def format_one(r: Reply) -> str:
    lines = [f"[{r.key}]  {r.situation}", f"  WHEN: {r.when}", ""]
    lines += ["  " + line if line else "" for line in r.body.split("\n")]
    if r.note:
        lines += ["", f"  NOTE: {r.note}"]
    return "\n".join(lines)


def format_all() -> str:
    out = [
        "BUYER REPLY DRAFTS",
        "",
        f"  {len(REPLIES)} situations. Drafts only - nothing here sends anything.",
        "  Fill any {braces} before using. No off-platform contact, no guarantees, no claim the",
        "  verified profile does not back.",
        "",
    ]
    for r in REPLIES:
        out.append(format_one(r))
        out.append("")
        out.append("-" * 74)
        out.append("")
    return "\n".join(out)
