# Agentic EDA Plan

## Objective

Add an agent-assisted exploratory data analysis workflow that remains reproducible
inside Jupyter. The notebook should compute facts deterministically, present
those facts to a local model for interpretation, and require human review before
any analytical conclusion becomes a project decision.

The first notebook is a diagnostic instrument, not a feature-engineering or
modeling notebook.

## Workflow

```text
canonical observed panel
          |
          v
Jupyter deterministic profiling
          |
          v
structured EDA findings and plots
          |
          v
local Qwen model via LangGraph
          |
          v
proposed questions, risks, and follow-up analyses
          |
          v
human review and recorded decisions
```

## Notebook

The initial notebook is:

```text
notebooks/01_agentic_eda.ipynb
```

It is designed to work before the final processed panel exists. Set the input
path when `data/processed/ercot_hourly_panel/` is available, then run the
notebook from the repository root or configure `PROJECT_ROOT` explicitly.

## What the notebook computes without an agent

The notebook must calculate and display, using ordinary Python:

- input path and file inventory;
- schema and data types;
- row counts and time range;
- timestamp monotonicity, duplicates, and gaps;
- missingness by column and by time period;
- load distribution and extreme values;
- weather-variable distributions and ranges;
- weather coverage diagnostics;
- correlation summaries, with warnings about correlation not implying causation;
- seasonal and diurnal summaries;
- station or spatial diagnostics when station-level data is supplied.

Each result should be serializable as JSON so the agent receives a compact,
traceable evidence bundle rather than an opaque dataframe dump.

## Agent responsibilities

The LangGraph/Qwen layer may:

- prioritize follow-up questions;
- explain unusual patterns found by deterministic checks;
- identify possible data-quality risks;
- suggest plots or stratifications to inspect;
- propose hypotheses for later modeling;
- identify analyses that could create leakage.

The agent must cite the finding IDs or profiling outputs supporting each claim.
It must distinguish observed facts, hypotheses, and recommended checks.

## Human approval boundaries

The agent must not silently:

- change the data;
- impute missing values;
- change units or timestamp interpretation;
- remove outliers;
- choose a forecast target or horizon;
- create model features;
- declare a causal relationship;
- write conclusions directly into the canonical data product.

A reviewed decision should be recorded in a Markdown or JSON report under:

```text
reports/eda/
```

Suggested report sections are `facts`, `hypotheses`, `risks`, `approved_followups`,
and `rejected_or_deferred_actions`.

## LangGraph design

The first graph can use these states:

```text
load_data -> profile_data -> render_diagnostics -> agent_review
                                      |
                                      v
                              human_review
                                /      \
                         approve       revise
                            |             |
                     save_eda_report  run_requested_check
```

Tools should be narrow and read-only initially:

```text
inventory_panel()
profile_schema()
profile_time_axis()
profile_missingness()
profile_distributions()
render_standard_plots()
validate_eda_claim(claim_id)
```

The notebook can call the graph through a local adapter. The adapter should
return structured messages and preserve the profiling evidence used by the
model. If the local Qwen server or LangGraph is unavailable, the notebook must
still complete the deterministic EDA cells and save the evidence bundle.

## Reproducibility rules

- Pin or record the input artifact/version used for each EDA run.
- Record the notebook execution date and code revision.
- Use fixed random seeds for sampling.
- Never sample rows without recording the sampling rule.
- Keep plots derived from canonical data separate from feature experiments.
- Do not use future observations to justify a forecasting decision without
  labeling the analysis as retrospective EDA.
- Save structured findings and figures, not just notebook output cells.

## Notebook versioning policy

Notebook files belong in Git; datasets belong in DVC. The repository will adopt
`nbstripout` as a future pre-commit policy so transient cell outputs are removed
before commits. It is intentionally documented here before being installed or
added to repository hooks.

Until that policy is wired in, clear large outputs manually before committing.
Do not embed full-corpus tables, plots, or generated evidence in the notebook;
save them under `reports/eda/` instead. Keep reusable transformations in
`src/` or `scripts/`, with the notebook serving as an analysis narrative.

## First implementation sequence

1. Run `notebooks/01_agentic_eda.ipynb` in deterministic mode against a small
   fixture or the processed panel when available.
2. Add tests for the profiling helpers, especially timestamp and missingness
   calculations.
3. Save an EDA evidence bundle and standard plots under `reports/eda/`.
4. Add the optional local Qwen/LangGraph adapter behind an explicit setting.
5. Add human-review persistence for approved follow-up analyses.
6. Only after that, add tools that can launch bounded downstream analyses.

## Definition of done

The EDA phase is complete when:

- the notebook runs without an agent or network connection;
- every agent claim can be traced to a deterministic finding;
- the notebook distinguishes facts from hypotheses;
- human decisions are persisted outside transient chat context;
- no EDA action mutates raw, interim, or processed data;
- the same input artifact produces the same evidence bundle and plots.
