"""OCR evaluation on a folder of label photos with manually checked reference text (Stage 3, point 21).

Folder layout:
    <dir>/manifest.json   {"description": ..., "cases": [{"image": "p01.jpg", "reference": "<ingredient statement
                           as printed>", "allergens": ["milk", ...], "conditions": ["glare", "curved"], "notes": ""}]}
    <dir>/<images>

Metrics per photo and per condition: character error rate (CER) and word error rate (WER) of the
ingredient statement the pipeline extracted, ingredient extraction precision/recall (as normalized
items), and downstream allergen agreement (allergens linked from the OCR text vs from the reference).

    python -m app.evaluation.ocr_eval --generate DIR    # synthesize a controlled set (not real photos)
    python -m app.evaluation.ocr_eval DIR [--report out.md]
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont
from rapidfuzz.distance import Levenshtein

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def _norm(t: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9%(),:;. ]", " ", t.lower()).split())


def cer(ref: str, hyp: str) -> float:
    r, h = _norm(ref), _norm(hyp)
    return Levenshtein.distance(r, h) / max(1, len(r))


def wer(ref: str, hyp: str) -> float:
    r, h = _norm(ref).split(), _norm(hyp).split()
    return Levenshtein.distance(r, h) / max(1, len(r))


def confusions(ref: str, hyp: str) -> Counter:
    r, h = _norm(ref), _norm(hyp)
    out: Counter = Counter()
    for op in Levenshtein.editops(r, h):
        if op.tag == "replace":
            out[f"{r[op.src_pos]}→{h[op.dest_pos]}"] += 1
    return out


def _items(kb, text: str) -> set[str]:
    from app.engine.normalize import normalize_ingredients

    return {n.text for n in normalize_ingredients(kb, text) if n.counts and n.text}


def _allergens(kb, text: str, ocr: bool) -> set[str]:
    from app.engine.assess import resolution_out
    from app.engine.normalize import normalize_ingredients

    out: set[str] = set()
    for n in normalize_ingredients(kb, text, ocr=ocr):
        r = resolution_out(kb, "e", n)
        out |= set(r.allergens) | set(r.possible_allergens)
    return out


def evaluate_dir(d: Path) -> dict:
    from app.evaluation.seed_kb import build_kb
    from app.packaged.images import load_image
    from app.packaged.ocr import extract_ingredient_section, run_ocr

    kb = build_kb()
    manifest = json.loads((d / "manifest.json").read_text())
    rows, by_cond, conf = [], defaultdict(list), Counter()
    for c in manifest["cases"]:
        img = load_image((d / c["image"]).read_bytes(), 20 * 1024 * 1024)
        ocr = run_ocr(img.image)
        section = extract_ingredient_section(ocr.text) if ocr.text else None
        hyp = section.ingredients_text if section else ""
        ref = c["reference"]
        ri, hi = _items(kb, ref), _items(kb, hyp)
        truth = set(c.get("allergens") or _allergens(kb, ref, False))
        got = _allergens(kb, hyp, True)
        row = {"image": c["image"], "conditions": c.get("conditions", []), "ocr_confidence": round(ocr.confidence, 3),
               "heading_found": bool(section and section.found_heading), "image_issues": img.issues,
               "cer": round(cer(ref, hyp), 4), "wer": round(wer(ref, hyp), 4),
               "item_precision": round(len(ri & hi) / len(hi), 4) if hi else 0.0,
               "item_recall": round(len(ri & hi) / len(ri), 4) if ri else 1.0,
               "allergens_missed": sorted(truth - got), "allergens_extra": sorted(got - truth)}
        rows.append(row)
        conf += confusions(ref, hyp)
        for cond in row["conditions"] or ["none"]:
            by_cond[cond].append(row)

    def agg(rs):
        n = len(rs)
        return {"photos": n, "cer": round(sum(r["cer"] for r in rs) / n, 4), "wer": round(sum(r["wer"] for r in rs) / n, 4),
                "item_recall": round(sum(r["item_recall"] for r in rs) / n, 4),
                "photos_missing_an_allergen": sum(1 for r in rs if r["allergens_missed"])}
    return {"description": manifest.get("description", ""), "overall": agg(rows),
            "by_condition": {k: agg(v) for k, v in sorted(by_cond.items())},
            "top_confusions": conf.most_common(10), "photos": rows}


# ---- a controlled generated set (clearly not real photos) ----

LABELS = [
    ("Wholegrain oat flakes (38%), wheat flour, sugar, palm oil, butter (milk), golden syrup, salt", ["gluten", "wheat", "milk"]),
    ("Sugar, cocoa butter, whole milk powder, cocoa mass, hazelnuts, emulsifier: soya lecithin", ["milk", "tree_nuts", "soy"]),
    ("Rice, sunflower oil, salt, paprika, onion powder, garlic powder", []),
    ("Chickpeas, tahini (sesame), lemon juice, garlic, olive oil, salt", ["sesame"]),
    ("Wheat flour, water, egg, rapeseed oil, salt, mustard seeds", ["gluten", "wheat", "eggs", "mustard"]),
]
CONDITIONS = {
    "clean": lambda im: im,
    "blur": lambda im: im.filter(ImageFilter.GaussianBlur(1.2)),
    "rotated": lambda im: im.rotate(4, expand=True, fillcolor="white"),
    "low_light": lambda im: ImageEnhance.Brightness(im).enhance(0.45),
    "jpeg": lambda im: _jpeg(im, 25),
}


def _jpeg(im, q):
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=q)
    return Image.open(io.BytesIO(buf.getvalue())).convert("RGB")


def _wrap(text: str, width: int = 44) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    return lines + [cur]


def generate(d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)
    font = ImageFont.truetype(FONT, 28)
    cases = []
    for i, (text, allergens) in enumerate(LABELS, 1):
        lines = ["INGREDIENTS: " + _wrap(text)[0], *_wrap(text)[1:]]
        base = Image.new("RGB", (1100, 120 + 52 * len(lines)), "white")
        dr = ImageDraw.Draw(base)
        for j, line in enumerate(lines):
            dr.text((40, 40 + j * 52), line, fill="black", font=font)
        for cond, fn in CONDITIONS.items():
            name = f"label{i}_{cond}.png"
            fn(base).save(d / name)
            cases.append({"image": name, "reference": text, "allergens": allergens, "conditions": [cond],
                          "notes": "generated image, not a real photo"})
    (d / "manifest.json").write_text(json.dumps({"description": "Generated labels under controlled conditions "
                                                 "(not real photos).", "cases": cases}, indent=1))


def render(r: dict) -> str:
    o = r["overall"]
    lines = ["# OCR evaluation", "", r["description"], "",
             f"{o['photos']} photos: mean CER {o['cer']:.3f}, mean WER {o['wer']:.3f}, ingredient recall {o['item_recall']:.3f}, "
             f"{o['photos_missing_an_allergen']} photo(s) where an allergen was missed.", "",
             "| Condition | Photos | CER | WER | Ingredient recall | Photos missing an allergen |", "|---|---|---|---|---|---|"]
    lines += [f"| {k} | {v['photos']} | {v['cer']:.3f} | {v['wer']:.3f} | {v['item_recall']:.3f} | {v['photos_missing_an_allergen']} |"
              for k, v in r["by_condition"].items()]
    lines += ["", "Most frequent character confusions: " + (", ".join(f"{k} ({n})" for k, n in r["top_confusions"]) or "none"), ""]
    misses = [p for p in r["photos"] if p["allergens_missed"]]
    if misses:
        lines += ["Photos where an allergen was missed:", ""] + [
            f"- {p['image']} ({', '.join(p['conditions'])}): missed {', '.join(p['allergens_missed'])}; "
            f"OCR confidence {p['ocr_confidence']}" for p in misses]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dir", type=Path)
    ap.add_argument("--generate", action="store_true")
    ap.add_argument("--report", type=Path)
    a = ap.parse_args(argv)
    if a.generate:
        generate(a.dir)
        print(f"Generated a controlled set in {a.dir}")
        return 0
    r = evaluate_dir(a.dir)
    text = render(r)
    if a.report:
        a.report.write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
