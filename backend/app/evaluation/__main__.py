"""Run the benchmark and the release gate.

    python -m app.evaluation                      # seed knowledge base; exit 1 if the gate fails
    python -m app.evaluation --kb database        # the live knowledge base (DATABASE_URL)
    python -m app.evaluation --report r.md --json r.json
    python -m app.evaluation --update-baseline    # accept the current metrics as the new baseline
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.evaluation.gate import check, flatten, write_baseline
from app.evaluation.report import render
from app.evaluation.runner import evaluate
from app.evaluation.seed_kb import build_kb


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.evaluation")
    ap.add_argument("--kb", choices=["seed", "database"], default="seed")
    ap.add_argument("--report", type=Path, help="write a Markdown report here")
    ap.add_argument("--json", type=Path, help="write metrics and gate checks as JSON here")
    ap.add_argument("--update-baseline", action="store_true")
    args = ap.parse_args(argv)

    if args.kb == "database":
        from app.db.session import SessionLocal
        from app.engine.service import load_knowledge_base

        with SessionLocal() as db:
            kb = load_knowledge_base(db)
        label = f"live knowledge base (version {kb.version})"
    else:
        kb, label = build_kb(), "seed knowledge base"

    results = evaluate(kb)
    if args.update_baseline:
        write_baseline(results)
        print("Baseline updated.")
    gate = check(results)
    report = render(results, gate, label)
    if args.report:
        args.report.write_text(report)
    if args.json:
        args.json.write_text(json.dumps({
            "passed": gate.passed, "metrics": flatten(results),
            "checks": [c.__dict__ for c in gate.checks],
            "failures": {a: r.failures for a, r in results.items()},
        }, indent=1, default=str))
    for c in gate.failed:
        print(f"FAILED [{c.level}] {c.metric} = {c.value} (required {c.rule}) {c.why}")
    print(f"Release gate {'PASSED' if gate.passed else 'FAILED'}: "
          f"{len(gate.checks) - len(gate.failed)}/{len(gate.checks)} checks passed.")
    return 0 if gate.passed else 1


if __name__ == "__main__":
    sys.exit(main())
