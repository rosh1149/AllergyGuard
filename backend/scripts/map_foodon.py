"""Map AllergyGuard ingredient concepts to FoodOn ids.

Usage:
    git clone --depth 1 --filter=blob:none --no-checkout https://github.com/FoodOntology/foodon
    (cd foodon && git checkout HEAD -- foodon-synonyms.tsv)
    python scripts/map_foodon.py foodon/foodon-synonyms.tsv <foodon commit> <commit date>

A concept gets a FoodOn id only when its own name equals a FoodOn class *label* (case-insensitive) and
that label belongs to exactly one FOODON class. Labels matching only an alias are listed as review
candidates (they may be narrower classes), never as the concept's id. Synonyms and partial matches
are not used: a wrong id is worse than none. Writes app/seed/foodon_mappings.json.
"""

import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.seed.concept_data import CONCEPTS  # noqa: E402

LABEL = re.compile(r'^"?(.*?)"?(@[\w-]+)?$')

# Exact label matches whose FoodOn class means something else (checked against the class's parents).
EXCLUDED = {
    "kiwi": "FoodOn 'kiwi' is the bird (parent: ratite), not the fruit",
    "lamb": "FoodOn 'lamb' is the animal (parent: sheep), not lamb meat",
    "praline": "FoodOn 'praline' is a pecan candy; this concept is the almond/hazelnut confection",
}


def main(tsv: str, commit: str, date: str) -> None:
    labels: dict[str, set[str]] = defaultdict(set)
    names: dict[str, str] = {}
    parents: dict[str, set[str]] = defaultdict(set)
    with open(tsv, encoding="utf-8") as f:
        rows = csv.reader(f, delimiter="\t")
        next(rows)
        for r in rows:
            if len(r) < 4:
                continue
            cid = r[0].strip("<>").rsplit("/", 1)[-1]
            if r[1]:
                parents[cid].add(r[1].strip("<>").rsplit("/", 1)[-1])
            if r[2].strip('"') != "label" or not r[3]:
                continue
            text = LABEL.match(r[3]).group(1).strip().lower()
            names.setdefault(cid, text)
            if cid.startswith("FOODON_"):
                labels[text].add(cid)

    out = {"source": "FoodOn foodon-synonyms.tsv", "foodon_commit": commit, "foodon_date": date,
           "match_rule": "concept name equals the label of exactly one FOODON class", "mappings": {}}
    out["review_candidates"] = {}
    out["excluded"] = {}
    for slug, (name, aliases, _) in CONCEPTS.items():
        ids = labels.get(name.lower(), set())
        if len(ids) == 1 and slug in EXCLUDED:
            out["excluded"][slug] = {"foodon_id": next(iter(ids)).replace("_", ":"), "reason": EXCLUDED[slug]}
            continue
        if len(ids) == 1:
            cid = next(iter(ids))
            out["mappings"][slug] = {
                "foodon_id": cid.replace("_", ":"),
                "foodon_label": name.lower(),
                "foodon_parents": sorted(names.get(p, p) for p in parents.get(cid, ())),
            }
            continue
        # An alias match may be a narrower class (alias "cow milk" for concept "milk"): never used as the
        # concept's id, only listed for a knowledge reviewer to decide.
        cands = {a.lower(): sorted(labels[a.lower()]) for a in aliases if a.lower() in labels}
        if cands:
            out["review_candidates"][slug] = cands
    dest = Path(__file__).resolve().parents[1] / "app" / "seed" / "foodon_mappings.json"
    dest.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print(f"{len(out['mappings'])} of {len(CONCEPTS)} concepts mapped -> {dest}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
