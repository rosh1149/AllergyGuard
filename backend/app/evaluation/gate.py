"""Release gate: compare benchmark metrics with the release criteria and the stored baseline."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from app.evaluation.runner import AreaResult

HERE = Path(__file__).parent
CRITERIA = HERE / "release_criteria.json"
BASELINE = HERE / "baseline.json"


@dataclass
class Check:
    metric: str
    value: float | int | None
    rule: str
    level: str  # critical | quality | baseline | dataset
    passed: bool
    why: str = ""


@dataclass
class GateResult:
    checks: list[Check] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if not c.passed]


def flatten(results: dict[str, AreaResult]) -> dict[str, float | int | None]:
    return {f"{area}.{k}": v for area, r in results.items() for k, v in r.metrics.items()}


def case_counts(results: dict[str, AreaResult]) -> dict[str, int]:
    return {area: r.cases for area, r in results.items()}


def _meets(value, spec: dict) -> bool:
    if value is None:
        return False
    if "min" in spec and value < spec["min"]:
        return False
    if "max" in spec and value > spec["max"]:
        return False
    return True


def _rule(spec: dict) -> str:
    return " and ".join(f"{'>=' if k == 'min' else '<='} {spec[k]}" for k in ("min", "max") if k in spec)


def check(results: dict[str, AreaResult], criteria: dict | None = None, baseline: dict | None = None) -> GateResult:
    criteria = criteria or json.loads(CRITERIA.read_text())
    if baseline is None and BASELINE.exists():
        baseline = json.loads(BASELINE.read_text())
    metrics = flatten(results)
    gate = GateResult()
    tol = criteria.get("baseline_tolerance", 0.0)
    for level in ("critical", "quality"):
        for name, spec in criteria[level].items():
            value = metrics.get(name)
            gate.checks.append(Check(name, value, _rule(spec), level, _meets(value, spec), spec.get("why", "")))
            if baseline and name in baseline.get("metrics", {}) and baseline["metrics"][name] is not None:
                base = baseline["metrics"][name]
                if "min" in spec:
                    ok = value is not None and value >= base - tol
                    rule = f">= baseline {base} - {tol}"
                else:
                    ok = value is not None and value <= base + tol
                    rule = f"<= baseline {base} + {tol}"
                gate.checks.append(Check(name, value, rule, "baseline", ok, "No regression against the accepted baseline."))
    if baseline:
        for area, n in baseline.get("cases", {}).items():
            got = case_counts(results).get(area, 0)
            gate.checks.append(Check(f"{area}.cases", got, f">= {n}", "dataset", got >= n,
                                     "Benchmark cases must not be removed to pass the gate."))
    return gate


def write_baseline(results: dict[str, AreaResult], path: Path = BASELINE) -> None:
    path.write_text(json.dumps({"metrics": flatten(results), "cases": case_counts(results)}, indent=1, sort_keys=True) + "\n")


def critical_failures(results: dict[str, AreaResult]) -> list[str]:
    """Safety failures only (used by the knowledge-change approval gate)."""
    gate = check(results, baseline={})
    out = [f"{c.metric} = {c.value} (required {c.rule})" for c in gate.failed if c.level == "critical"]
    if out:
        for area in ("safety", "evidence_fusion", "uncertainty", "semantic_reasoning", "provenance"):
            out += results[area].failures[:5]
    return out
