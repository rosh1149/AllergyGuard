"""The held-out test set stays frozen and well-formed. This test never runs the engine on it."""

from app.evaluation.heldout_runner import load, main, verify_manifest
from app.seed.allergen_data import ALLERGENS
from app.engine.types import SourceType

CODES = {a["code"] for a in ALLERGENS}
TRUTH = {"present", "cross_contact", "absent", "undeterminable"}


def test_manifest_unchanged():
    assert verify_manifest() == []


def test_cases_well_formed():
    for name in ("ingredients", "ocr"):
        for c in load(name):
            assert c["id"] and c["input"] and c["justification"]
            assert set(c["must"]) <= CODES and set(c["must_not"]) <= CODES
            assert not set(c["must"]) & set(c["must_not"])
    ids = set()
    for c in load("scenarios"):
        assert c["id"] not in ids and c["justification"]
        ids.add(c["id"])
        assert set(c["truth"]) <= CODES and set(c["truth"].values()) <= TRUTH
        for s in c["sources"]:
            SourceType(s["type"])


def test_runner_refuses_without_final(capsys):
    assert main([]) == 1
    assert "final evaluation only" in capsys.readouterr().out
