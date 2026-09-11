#!/usr/bin/env python3
"""Refuse to ship a commit that contains a credential.

Runs in CI over every tracked file. The storage layer also screens every write
(``aicc.storage.assert_safe_to_commit``); this is the second line, because a leak that only CI
catches has already been written to disk, and one that neither catches is in git history forever.

Exit 1 on any finding.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("Anthropic API key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("Claude Code OAuth token", re.compile(r"sk-ant-oat[A-Za-z0-9_\-]{10,}")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("GitHub fine-grained PAT", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}\b")),
    ("AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Private key block", re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("Stripe key", re.compile(r"\b[sr]k_live_[0-9a-zA-Z]{20,}\b")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b")),
    ("Possible payment card", re.compile(r"(?<!\d)(?:4\d{15}|5[1-5]\d{14}|3[47]\d{13})(?!\d)")),
    ("Possible US SSN", re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")),
    ("Session cookie jar", re.compile(r"(?i)\b(?:set-cookie|__Secure-|__Host-)[A-Za-z0-9_\-]*=")),
]

# Files that legitimately contain the patterns above: this scanner, its tests, and the docs
# that explain what is blocked.
ALLOWLIST = {
    "scripts/secret_scan.py",
    "src/aicc/storage.py",
    "tests/test_safety.py",
    "docs/SECURITY.md",
    "SECURITY.md",
}

BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".gz", ".woff", ".woff2", ".ico", ".webp"}


def tracked_files() -> list[Path]:
    try:
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True)  # noqa: S607
        return [ROOT / line for line in out.stdout.splitlines() if line]
    except (subprocess.CalledProcessError, FileNotFoundError):
        return [p for p in ROOT.rglob("*") if p.is_file() and ".git" not in p.parts]


def main() -> int:
    findings: list[str] = []
    scanned = 0

    for path in tracked_files():
        rel = path.relative_to(ROOT).as_posix()
        if rel in ALLOWLIST or path.suffix.lower() in BINARY_SUFFIXES or not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        scanned += 1
        for label, pattern in PATTERNS:
            for m in pattern.finditer(text):
                line = text[: m.start()].count("\n") + 1
                findings.append(f"{rel}:{line}: {label}")

    # Third-party contact details must never reach the committed store. Redaction happens at
    # ingest (aicc.privacy); this is the check that proves it held. The repository is public and
    # git history is permanent, so a harvested email is not recoverable by deleting it later.
    try:
        import sys as _sys

        _sys.path.insert(0, str(ROOT / "src"))
        from aicc.privacy import contains_contact_details

        for path in tracked_files():
            rel = path.relative_to(ROOT).as_posix()
            if not rel.startswith("data/") or not path.exists():
                continue
            try:
                body = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            # Our own bot address is ours to publish.
            body = body.replace("actions@users.noreply.github.com", "")
            if contains_contact_details(body):
                findings.append(f"{rel}: third-party contact details in the committed store")
    except ImportError:
        findings.append("could not import aicc.privacy to check the store for contact details")

    # Structural checks: some things must never be tracked at all.
    for path in tracked_files():
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith(("workspaces/", "jobs_private/", "client_files/")):
            findings.append(f"{rel}: client workspace content must never be committed")
        if rel == ".env" or rel.endswith("/.env"):
            findings.append(f"{rel}: .env must never be committed")

    if findings:
        print(f"SECRET SCAN FAILED - {len(findings)} finding(s) across {scanned} files:\n")
        for f in findings:
            print(f"  {f}")
        return 1

    print(f"Secret scan clean ({scanned} files).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
