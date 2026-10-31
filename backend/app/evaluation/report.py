"""Markdown report of a benchmark run and its gate result."""

from __future__ import annotations

from datetime import UTC, datetime

from app.evaluation.gate import GateResult
from app.evaluation.runner import AreaResult

AREA_TITLES = {
    "normalization": "Ingredient normalization", "ocr_correction": "OCR correction", "synonyms": "Synonym resolution",
    "ontology_mapping": "Ontology (concept) mapping", "semantic_reasoning": "Semantic reasoning",
    "compound_ingredients": "Compound ingredients", "evidence_fusion": "Evidence fusion", "provenance": "Provenance",
    "uncertainty": "Uncertainty", "safety": "Safety (end-to-end allergen dataset)",
}


def _fmt(v) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return f"{v:.4f}".rstrip("0").rstrip(".") if v not in (0.0, 1.0) else f"{v:.1f}"
    return str(v)


def render(results: dict[str, AreaResult], gate: GateResult, kb_label: str) -> str:
    lines = [
        "# AllergyGuard evaluation report",
        "",
        f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC against the {kb_label}.",
        "",
        f"**Release gate: {'PASSED' if gate.passed else 'FAILED'}** "
        f"({len(gate.checks) - len(gate.failed)}/{len(gate.checks)} checks passed)",
        "",
    ]
    if gate.failed:
        lines += ["## Failed checks", "", "| Metric | Value | Required | Level |", "|---|---|---|---|"]
        lines += [f"| {c.metric} | {_fmt(c.value)} | {c.rule} | {c.level} |" for c in gate.failed]
        lines.append("")
    rules = {c.metric: c for c in gate.checks if c.level in ("critical", "quality")}
    lines += ["## Metrics", "", "| Area | Cases | Metric | Value | Release criterion |", "|---|---|---|---|---|"]
    for area, r in results.items():
        for i, (k, v) in enumerate(r.metrics.items()):
            c = rules.get(f"{area}.{k}")
            crit = f"{c.rule} ({c.level}){'' if c.passed else ' **FAILED**'}" if c else ""
            lines.append(f"| {AREA_TITLES[area] if i == 0 else ''} | {r.cases if i == 0 else ''} | {k} | {_fmt(v)} | {crit} |")
    lines += ["", "## Cases not matching the expected result", ""]
    any_fail = False
    for area, r in results.items():
        if r.failures:
            any_fail = True
            lines.append(f"**{AREA_TITLES[area]}**")
            lines += [f"- {f}" for f in r.failures]
            lines.append("")
    if not any_fail:
        lines.append("None.")
    return "\n".join(lines) + "\n"
