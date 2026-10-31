"""Evaluation benchmark and release gate for the allergen engine (Step F).

Datasets live in `datasets/` (JSON, versioned with the code). `runner.evaluate()` computes metrics
per area, `gate.check()` compares them with `release_criteria.json` and `baseline.json`.

    python -m app.evaluation               # report + gate (exit code 1 when the gate fails)
    python -m app.evaluation --update-baseline
"""
