# A model pipeline that publishes its own limitations

Calibration reports, a model card and a written record of known limitations, published alongside the projections rather than instead of them.

## The problem

Any system that outputs a number invites more confidence than the number deserves. The engineering problem is not producing the estimate; it is making its uncertainty impossible to miss.

## Approach

- A model card states what each model does and how well it has actually performed, and is meant to be read before the output is used.
- Calibration reports are regenerated from a command rather than written by hand, so they cannot drift away from the model they describe.
- Known limitations are a maintained document, not a footnote.
- Markets the model has no measured edge in are shown as comparisons only, explicitly carrying no recommendation.

## Outcome

- Every published figure is traceable to the run and the method that produced it.
- The public dashboard labels model output as model output on each individual card, not once in a footer.

**Stack:** python, pandas, model calibration, backtesting, reporting

## Evidence you can check yourself

- A public dashboard that labels every projection as model output and disclaims guarantees in its own repository description.  
  nfl-dashboard - <https://amercado19.github.io/nfl-dashboard/>

## Available on request

These live in private repositories, so they are offered for a walkthrough rather than
asserted to someone who cannot open them:

- MODEL_CARD.md, docs/CALIBRATION_REPORT.md and KNOWN_LIMITATIONS.md, each regenerable from a CLI command.  
  nfl-pipeline (private)

---

*Relevant to: data_analysis, model_pipeline, reporting, financial_model.*
