"""Stage 4 tooling, checked on the development set only (the held-out set is never evaluated in tests)."""

import pytest

from app.engine import AllergyProfile, EvidenceSource, Outcome, SourceType, assess
from app.engine.ablation import Ablation, ablated, current
from app.evaluation.baselines import BASELINES, ingredient_links
from app.evaluation.experiments import ABLATIONS, run
from tests.kb_fixture import build_kb

NL, U, P = Outcome.NOT_LISTED_IN_VERIFIED_RECORDS, Outcome.UNKNOWN, Outcome.PRESENT


@pytest.fixture(scope="module")
def kb():
    return build_kb()


def lab(text, **kw):
    return EvidenceSource(id="l", type=SourceType.label_manual, name="L", ingredients_text=text, **kw)


def one(kb, srcs, code):
    return assess(kb, srcs, AllergyProfile({code})).your_allergens[0].outcome


def test_ablation_is_off_by_default_and_scoped():
    assert current() == Ablation()
    with ablated(no_ontology=True):
        assert current().no_ontology and current().name == "no_ontology"
    assert current() == Ablation()
    assert [n for n, _ in ABLATIONS][0] == "full" and len(ABLATIONS) == 7


@pytest.mark.parametrize("flag, sources, code, full, off", [
    ("no_ontology", [lab("rice, bean curd")], "soy", P, NL),
    ("no_normalization", [lab("rice, mayonaise")], "eggs", P, U),
    ("no_uncertainty", [lab("rice, zzqx paste")], "milk", U, NL),
    ("no_uncertainty", [lab("rice, salt", complete=False)], "milk", U, NL),
    ("no_source_reliability", [EvidenceSource(id="p", type=SourceType.public_recipe, name="P", ingredients=["rice", "salt"])], "milk", U, NL),
])
def test_each_switch_removes_its_component(kb, flag, sources, code, full, off):
    assert one(kb, sources, code) == full
    with ablated(**{flag: True}):
        assert one(kb, sources, code) == off
    assert one(kb, sources, code) == full  # nothing leaks out of the context


def test_no_conflicts_switch(kb):
    srcs = [lab("rice, salt, sugar, vinegar"),
            EvidenceSource(id="d", type=SourceType.product_database, name="D", ingredients_text="potato, sunflower oil, paprika, onion")]
    assert one(kb, srcs, "milk") == U
    with ablated(no_conflicts=True):
        assert one(kb, srcs, "milk") == NL


def test_baselines(kb):
    srcs = [lab("sugar, sodium caseinate, coconut milk")]
    assert BASELINES["b1_exact"](kb, srcs, {"milk"})["milk"] == "PRESENT"  # only because of "coconut milk"
    assert ingredient_links("b1_exact", kb, "sodium caseinate") == set()
    assert ingredient_links("b1_exact", kb, "coconut milk") == {"milk"}  # the false link a name matcher makes
    assert ingredient_links("b3_knowledge", kb, "coconut milk") == set()
    assert ingredient_links("proposed", kb, "lecithin") == {"soy", "eggs"}  # possible links count for the proposed system
    assert ingredient_links("b3_knowledge", kb, "lecithin") == set()


def test_dev_experiment_runs_without_heldout():
    man, res = run(include_heldout=False)
    assert res["set"] == "development safety set" and man["datasets"]["heldout"] == "not used"
    assert set(res["comparison"]) == {"proposed", "b1_exact", "b2_synonyms", "b3_knowledge"}
    assert res["comparison"]["proposed"]["fn"] == 0 and set(res["ablations"]) == {n for n, _ in ABLATIONS}
    assert res["performance"]["ms_p95"] > 0 and "heldout_ingredients" not in res
