# A test suite and CI gate that refuse to publish a broken build

Lint, type checking, tests and a verify-before-publish step, so a broken build never replaces a working page.

## The problem

Automated publishing turns a small bug into a live one instantly. Without a gate, the fastest deployment pipeline is also the fastest way to break production.

## Approach

- ruff, mypy and pytest run in CI on every change, not only locally.
- A verification step inspects the built artifact for the markers that prove it rendered, and refuses to deploy when they are missing.
- Secret scanning and a dependency audit run in the same pipeline.
- Documentation for conventions, decisions and deployment is kept in the repository so the next person - or the next agent - does not have to guess.

## Outcome

- A failed verification stops the deployment rather than replacing a working dashboard with a broken one.
- The same gates are reused across projects rather than reinvented per repository.

**Stack:** pytest, ruff, mypy, github actions, ci/cd

## Evidence you can check yourself

- This repository: the same gate structure, fully public and inspectable, including the publish gate that refuses a bad build.  
  ai-income-control-center - <https://github.com/amercado19/ai-income-control-center>

## Available on request

These live in private repositories, so they are offered for a walkthrough rather than
asserted to someone who cannot open them:

- 206 test-related files across the NFL project, with CI running the full suite on each change.  
  nfl-pipeline (private)

---

*Relevant to: testing, ci_cd, data_pipeline, data_engineering, automation.*
