"""Deterministic allergen assessment.

Rules (per allergen, first match wins):
  1. Any presence evidence from an authoritative source whose identity is trusted -> PRESENT
  2. Any cross-contact evidence (declared or "may contain")                         -> POSSIBLE_CROSS_CONTACT
  3. Presence evidence only from candidate/untrusted sources, or only through a
     "possible" source relationship (lecithin may come from soy)                     -> UNKNOWN
  4. A "free-from" claim is the only thing mentioning it                             -> UNKNOWN
  5. At least one complete, reliable, verified ingredient source and no
     ingredient-list mismatch between such sources                                   -> NOT_LISTED_IN_VERIFIED_RECORDS
  6. Otherwise                                                                       -> UNKNOWN

Missing evidence never produces a "safe" outcome; NOT_LISTED only means "not found in the
verified records that were available".

Ingredient items are resolved through the knowledge base (allergen terms + concept relationships), so
a finding can carry the chain that explains it (sodium caseinate, derived from casein, ... milk).
The allergen hierarchy then applies: a child allergen (wheat) implies its parent (cereals containing
gluten); a parent indicated without its specific source leaves each child as a possibility.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from itertools import combinations

from app.engine.knowledge import KnowledgeBase, custom_match
from app.engine.normalize import NormalizedIngredient, normalize_ingredients
from app.engine.text import (
    free_from_claims,
    normalize,
    split_precautions,
    word_set,
)
from app.engine.types import (
    ABSENCE_CAPABLE,
    AUTHORITATIVE,
    IDENTITY_DEPENDENT,
    MIN_IDENTITY_CONFIDENCE,
    MIN_SOURCE_CONFIDENCE,
    SEVERITY,
    SOURCE_TYPE_LABELS,
    AllergenAssessment,
    AllergyProfile,
    AnalysisContext,
    AnalysisResult,
    Conflict,
    EvidenceRef,
    EvidenceSource,
    IngredientResolution,
    Outcome,
    SourceSummary,
    SourceType,
)

LIST_MISMATCH_THRESHOLD = 0.5


@dataclass
class _Prepared:
    src: EvidenceSource
    items: list[str]
    precautions: list[str]
    trusted: bool  # authoritative and identity OK -> presence counts as PRESENT
    absence_ok: bool
    absence_reason: str = ""
    present_codes: set[str] = field(default_factory=set)
    normalized: list[NormalizedIngredient] = field(default_factory=list)


def _prepare(kb: KnowledgeBase, src: EvidenceSource, ctx: AnalysisContext) -> _Prepared:
    precautions: list[str] = []
    ocr = src.type == SourceType.label_ocr  # OCR correction only applies to text read from a photo
    normalized: list[NormalizedIngredient] = []
    for raw in src.ingredients:
        normalized.extend(normalize_ingredients(kb, raw, ocr=ocr))
    if src.ingredients_text:
        body, stmts = split_precautions(normalize(src.ingredients_text))
        precautions.extend(stmts)
        normalized.extend(normalize_ingredients(kb, body, ocr=ocr))
    seen: set[tuple[str, str]] = set()
    normalized = [n for n in normalized if (n.text, n.role) not in seen and not seen.add((n.text, n.role))]
    items = list(dict.fromkeys(n.text for n in normalized if n.counts))
    if src.cross_contact_text:
        cc = normalize(src.cross_contact_text)
        _, stmts = split_precautions(cc)
        precautions.extend(stmts or [cc])

    identity_ok = not (
        src.type in IDENTITY_DEPENDENT
        and ctx.identity_confidence is not None
        and ctx.identity_confidence < MIN_IDENTITY_CONFIDENCE
    )
    trusted = src.type in AUTHORITATIVE and identity_ok

    reason = ""
    if src.type not in ABSENCE_CAPABLE:
        reason = "This kind of source is not a verified record."
    elif not identity_ok:
        reason = "The product or dish may not have been identified correctly."
    elif not src.complete:
        reason = "The source does not provide a complete ingredient list."
    elif src.confidence < MIN_SOURCE_CONFIDENCE:
        reason = "The text could not be read reliably."
    elif not items:
        reason = "No ingredients were found in this source."
    return _Prepared(src, items, precautions, trusted, absence_ok=not reason, absence_reason=reason, normalized=normalized)


def _ref(
    p: _Prepared,
    kind: str,
    ingredient: str = "",
    term: str = "",
    detail: str = "",
    concept: str = "",
    path: str = "",
    certainty: str = "",
    n: NormalizedIngredient | None = None,
) -> EvidenceRef:
    return EvidenceRef(
        source_id=p.src.id,
        source_name=p.src.name,
        source_type=p.src.type,
        kind=kind,
        ingredient=ingredient,
        matched_term=term,
        detail=detail,
        concept=concept,
        relationship_path=path,
        certainty=certainty,
        matched_as=n.text if n is not None and n.text != n.original else "",
        match_method=n.method if n is not None else "",
    )


def _collect(
    kb: KnowledgeBase, prepared: list[_Prepared], custom_terms: list[str]
) -> tuple[dict[str, list[EvidenceRef]], list[str]]:
    """Evidence per allergen key ("<code>" or "custom:<term>"), plus non-specific ingredients."""
    ev: dict[str, list[EvidenceRef]] = defaultdict(list)
    nonspecific: list[str] = []
    # A custom allergy naming a known concept ("almond") also matches what derives from it (marzipan).
    custom_concepts = {t: kb.alias_to_concept[t] for t in custom_terms if t in kb.alias_to_concept}

    for p in prepared:
        presence_kind = "presence" if p.trusted else "candidate"
        for n in p.normalized:
            if not n.counts or n.resolution is None:
                continue
            item, shown = n.text, n.original
            claims = free_from_claims(item)
            claim_codes: set[str] = set()
            for c in claims:
                claim_codes |= kb.codes_in(c)
            for code in claim_codes:
                ev[code].append(_ref(p, "claim", shown, detail=f"'{shown}' claims to be free of it", n=n))

            res = n.resolution
            definite: set[str] = set()
            for cm, path in res.paths:
                code = path.allergen_code
                if code in claim_codes or code not in kb.allergens:
                    continue
                if path.certainty == "definite":
                    if code in definite:
                        continue
                    definite.add(code)
                    ev[code].append(_ref(p, presence_kind, shown, cm.alias, concept=cm.slug, path=path.text, certainty="definite", n=n))
                    if p.trusted:
                        p.present_codes.add(code)
                else:
                    ev[code].append(_ref(p, "possible_source", shown, cm.alias, concept=cm.slug, path=path.text, certainty="possible", n=n))
            for m in res.terms:
                if m.allergen_code in claim_codes or m.allergen_code in definite:
                    continue
                definite.add(m.allergen_code)
                ev[m.allergen_code].append(_ref(p, presence_kind, shown, m.term, n=n))
                if p.trusted:
                    p.present_codes.add(m.allergen_code)

            for term in custom_terms:
                key = f"custom:{term}"
                via = next((cm for cm in res.concepts if term in custom_concepts and kb.concept_reaches(cm.slug, custom_concepts[term])), None)
                if any(term in c for c in claims):
                    ev[key].append(_ref(p, "claim", shown, detail=f"'{shown}' claims to be free of it", n=n))
                elif custom_match(term, item):
                    ev[key].append(_ref(p, presence_kind, shown, term, n=n))
                elif via:
                    ev[key].append(_ref(p, presence_kind, shown, via.alias, concept=via.slug,
                                        path=f"{kb.concepts[via.slug].name}, made from or containing {term}", n=n))
            if res.status == "ambiguous_source" and n.role != "compound" and item not in nonspecific:
                nonspecific.append(item)

        for code in p.src.declared_allergens or []:
            if code in kb.allergens:
                ev[code].append(_ref(p, "declared" if p.trusted else "candidate", detail="Declared by the source"))
                if p.trusted:
                    p.present_codes.add(code)

        cc_kind = "cross_contact" if p.trusted else "candidate_cross_contact"
        for decl in p.src.cross_contact:
            if decl.allergen_code in kb.allergens:
                detail = f"{decl.level.capitalize()} cross-contact" + (f": {decl.description}" if decl.description else "")
                ev[decl.allergen_code].append(_ref(p, cc_kind, detail=detail))
        for stmt in p.precautions:
            for code in kb.codes_in(stmt):
                ev[code].append(_ref(p, cc_kind, detail=stmt))
            for term in custom_terms:
                if custom_match(term, stmt):
                    ev[f"custom:{term}"].append(_ref(p, cc_kind, detail=stmt))

    _apply_hierarchy(kb, ev)
    return ev, nonspecific


def _apply_hierarchy(kb: KnowledgeBase, ev: dict[str, list[EvidenceRef]]) -> None:
    """Child allergen evidence implies the parent; a parent named without its source makes each child possible."""
    for child, parents in kb.parents.items():
        for parent in parents:
            if child not in kb.allergens or parent not in kb.allergens:
                continue
            have = {(r.source_id, r.ingredient, r.kind) for r in ev.get(parent, [])}
            for r in list(ev.get(child, [])):
                if r.kind in ("presence", "declared", "candidate", "cross_contact", "candidate_cross_contact") and (r.source_id, r.ingredient, r.kind) not in have:
                    ev[parent].append(r.model_copy(update={
                        "relationship_path": (r.relationship_path + ", " if r.relationship_path else "")
                        + f"{kb.name(child)} is one of the {kb.name(parent).lower()}",
                    }))
            child_keys = {(r.source_id, r.ingredient) for r in ev.get(child, [])}
            for r in list(ev.get(parent, [])):
                unspecific = r.kind == "declared" or (r.kind == "presence" and not r.concept)
                if unspecific and (r.source_id, r.ingredient) not in child_keys:
                    ev[child].append(r.model_copy(update={
                        "kind": "possible_source",
                        "certainty": "possible",
                        "relationship_path": f"{kb.name(parent)} indicated without naming the source; it may be {kb.name(child).lower()}",
                    }))
                    child_keys.add((r.source_id, r.ingredient))


def _conflicts(kb: KnowledgeBase, prepared: list[_Prepared]) -> tuple[list[Conflict], bool]:
    conflicts: list[Conflict] = []

    # A source's own allergen declaration omits an allergen its ingredients indicate.
    for p in prepared:
        if p.src.declared_allergens is None or not p.trusted:
            continue
        res_list = [n.resolution for n in p.normalized if n.counts and n.resolution is not None]
        from_items = {m.allergen_code for r in res_list for m in r.terms}
        from_items |= {path.allergen_code for r in res_list for _, path in r.paths if path.certainty == "definite"}
        from_items &= set(kb.allergens)
        for code in sorted(from_items - set(p.src.declared_allergens)):
            conflicts.append(
                Conflict(
                    kind="declaration_mismatch",
                    allergen_code=code,
                    description=(
                        f"{p.src.name} lists an ingredient that indicates {kb.name(code)}, "
                        f"but its allergen declaration does not include {kb.name(code)}."
                    ),
                    source_ids=[p.src.id],
                )
            )

    eligible = [p for p in prepared if p.absence_ok]
    list_mismatch = False
    for a, b in combinations(eligible, 2):
        for code in sorted(a.present_codes ^ b.present_codes):
            has, lacks = (a, b) if code in a.present_codes else (b, a)
            conflicts.append(
                Conflict(
                    kind="allergen_disagreement",
                    allergen_code=code,
                    description=(
                        f"{has.src.name} indicates {kb.name(code)}, but {lacks.src.name} does not mention it."
                    ),
                    source_ids=[has.src.id, lacks.src.id],
                )
            )
        if len(a.items) >= 3 and len(b.items) >= 3:
            wa, wb = word_set(a.items), word_set(b.items)
            overlap = len(wa & wb) / max(1, min(len(wa), len(wb)))
            if overlap < LIST_MISMATCH_THRESHOLD:
                list_mismatch = True
                conflicts.append(
                    Conflict(
                        kind="ingredient_list_mismatch",
                        description=(
                            f"The ingredient lists from {a.src.name} and {b.src.name} differ substantially "
                            f"({overlap:.0%} overlap). They may describe different products, variants or "
                            "recipe versions."
                        ),
                        source_ids=[a.src.id, b.src.id],
                    )
                )
    return conflicts, list_mismatch


def _decide(
    name: str, refs: list[EvidenceRef], absence_supported: bool, absence_blocker: str
) -> tuple[Outcome, list[str]]:
    by_kind: dict[str, list[EvidenceRef]] = defaultdict(list)
    for r in refs:
        by_kind[r.kind].append(r)

    def where(r: EvidenceRef) -> str:
        return f"{SOURCE_TYPE_LABELS[r.source_type]}: {r.source_name}"

    def ing(r: EvidenceRef) -> str:
        """The ingredient as written, plus how it was read when normalization changed it."""
        if r.matched_as and r.matched_as != r.ingredient:
            how = {"ocr_correction": "OCR correction", "fuzzy": "closest known name", "spelling": "spelling"}.get(r.match_method, "")
            return f"'{r.ingredient}' (read as '{r.matched_as}'{', ' + how if how else ''})"
        return f"'{r.ingredient}'"

    present = by_kind["presence"] + by_kind["declared"]
    if present:
        reasons = []
        for r in present:
            if r.kind == "declared":
                reasons.append(f"{name} is declared as an allergen ({where(r)}).")
            elif r.relationship_path:
                reasons.append(f"Ingredient {ing(r)} indicates {name}: {r.relationship_path} ({where(r)}).")
            else:
                reasons.append(f"Ingredient {ing(r)} indicates {name} ({where(r)}).")
        return Outcome.PRESENT, _dedupe(reasons)

    cross = by_kind["cross_contact"] + by_kind["candidate_cross_contact"]
    if cross:
        reasons = []
        for r in cross:
            if r.kind == "cross_contact":
                suffix = ""
            elif r.source_type in AUTHORITATIVE:
                suffix = " This information may belong to a different item."
            else:
                suffix = " This source is unverified."
            if r.detail.lower().startswith(("known cross-contact", "possible cross-contact")):
                text = r.detail  # declared by the source, e.g. "Known cross-contact: shared fryer"
            else:
                text = f'Possible cross-contact: "{r.detail}"'  # a precautionary statement, quoted
            reasons.append(f"{text} ({where(r)}).{suffix}")
        return Outcome.POSSIBLE_CROSS_CONTACT, _dedupe(reasons)

    if by_kind["candidate"] or by_kind["possible_source"]:
        reasons = [
            f"'{r.ingredient or r.detail}' suggests {name} may be present, but the source is not verified "
            f"or may not match this item ({where(r)})."
            for r in by_kind["candidate"]
        ] + [
            f"{ing(r) if r.ingredient else repr(r.detail)} may contain {name}: {r.relationship_path}. The source is not stated ({where(r)})."
            for r in by_kind["possible_source"]
        ]
        return Outcome.UNKNOWN, _dedupe(reasons)

    if by_kind["claim"]:
        reasons = [
            f"{r.detail} ({where(r)}). AllergyGuard cannot verify free-from claims." for r in by_kind["claim"]
        ]
        return Outcome.UNKNOWN, _dedupe(reasons)

    if absence_supported:
        return Outcome.NOT_LISTED_IN_VERIFIED_RECORDS, [
            f"{name} was not found in the verified ingredient information available. "
            "This is not proof that it is absent."
        ]
    return Outcome.UNKNOWN, [absence_blocker]


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def assess(
    kb: KnowledgeBase,
    sources: list[EvidenceSource],
    profile: AllergyProfile,
    ctx: AnalysisContext | None = None,
) -> AnalysisResult:
    ctx = ctx or AnalysisContext()
    custom_terms = [normalize(t) for t in profile.custom_terms if normalize(t)]
    prepared = [_prepare(kb, s, ctx) for s in sources]
    evidence, nonspecific = _collect(kb, prepared, custom_terms)
    conflicts, list_mismatch = _conflicts(kb, prepared)

    eligible = [p for p in prepared if p.absence_ok]
    if not eligible:
        blocker = "There is no complete, verified ingredient information to check against."
        if prepared and all(p.absence_reason for p in prepared):
            blocker = "Not enough verified information: " + "; ".join(
                _dedupe([f"{p.src.name}: {p.absence_reason}" for p in prepared])
            )
    elif list_mismatch:
        blocker = "The available ingredient lists disagree, so absence cannot be established."
    else:
        blocker = ""
    absence_supported = bool(eligible) and not list_mismatch

    assessments: dict[str, AllergenAssessment] = {}
    for code, info in kb.allergens.items():
        outcome, reasons = _decide(info.name, evidence.get(code, []), absence_supported, blocker)
        assessments[code] = AllergenAssessment(
            code=code,
            name=info.name,
            outcome=outcome,
            outcome_label=outcome.label,
            reasons=reasons,
            evidence=evidence.get(code, []),
            regulated_in=kb.regulated_in(code),
        )
    for term in custom_terms:
        key = f"custom:{term}"
        outcome, reasons = _decide(term, evidence.get(key, []), absence_supported, blocker)
        if outcome == Outcome.NOT_LISTED_IN_VERIFIED_RECORDS:
            reasons.append(f"'{term}' is a custom allergy and was checked by ingredient name only.")
        assessments[key] = AllergenAssessment(
            code=key,
            name=term,
            is_custom=True,
            outcome=outcome,
            outcome_label=outcome.label,
            reasons=reasons,
            evidence=evidence.get(key, []),
        )

    rank = {o: i for i, o in enumerate(SEVERITY)}
    mine_keys = [c for c in profile.allergen_codes if c in kb.allergens] + [f"custom:{t}" for t in custom_terms]
    yours = sorted((assessments[k] for k in mine_keys), key=lambda a: (rank[a.outcome], a.name))
    others = sorted(
        (
            a
            for k, a in assessments.items()
            if k not in mine_keys and a.outcome in (Outcome.PRESENT, Outcome.POSSIBLE_CROSS_CONTACT)
        ),
        key=lambda a: (rank[a.outcome], a.name),
    )
    overall = yours[0].outcome if yours else Outcome.UNKNOWN

    limitations = list(ctx.limitations)
    if ctx.identity_confidence is not None and ctx.identity_confidence < MIN_IDENTITY_CONFIDENCE:
        limitations.append(
            "The product or dish could not be identified with confidence; information looked up for "
            "it may belong to a different item."
        )
    for p in prepared:
        if p.src.type == SourceType.label_ocr and p.src.confidence < MIN_SOURCE_CONFIDENCE:
            limitations.append("The label text could not be read reliably. Try a sharper, well-lit photo.")
        if p.src.type == SourceType.label_manual:
            limitations.append("The ingredient text was typed in manually; typos can affect the result.")
        if p.src.type == SourceType.public_recipe:
            limitations.append(
                "Public recipes describe how a dish is commonly made, not how this restaurant makes it."
            )
        if p.src.type == SourceType.restaurant_unverified:
            limitations.append("The restaurant's records for this dish have not been verified by staff yet.")
        if p.src.type == SourceType.customer_suggestion:
            limitations.append("Customer suggestions are unverified and have not been confirmed by the restaurant.")
    if nonspecific:
        limitations.append(
            "Some ingredients are general terms whose components are not listed: " + ", ".join(nonspecific) + "."
        )
    unresolved = _dedupe([n.original for p in prepared for n in p.normalized
                          if n.counts and n.resolution is not None and n.resolution.status == "unresolved"])
    if unresolved:
        limitations.append(
            "Not recognised by the ingredient knowledge base (checked by name only): " + ", ".join(unresolved) + "."
        )
    if not sources:
        limitations.append("No ingredient information was available.")

    summaries = [
        SourceSummary(
            id=p.src.id,
            type=p.src.type,
            type_label=SOURCE_TYPE_LABELS[p.src.type],
            name=p.src.name,
            url=p.src.url,
            ingredients=p.items,
            complete=p.src.complete,
            confidence=p.src.confidence,
            used_for_absence=p.absence_ok and not list_mismatch,
            not_used_for_absence_reason=p.absence_reason
            or ("Ingredient lists disagree between sources." if list_mismatch else ""),
        )
        for p in prepared
    ]

    return AnalysisResult(
        overall_outcome=overall,
        overall_label=overall.label,
        identity_label=ctx.identity_label,
        your_allergens=yours,
        other_allergens=others,
        sources=summaries,
        conflicts=conflicts,
        limitations=_dedupe(limitations),
        nonspecific_ingredients=nonspecific,
        ingredients=[resolution_out(kb, p.src.id, n) for p in prepared for n in p.normalized],
        knowledge_version=kb.version,
    )


def resolution_out(kb: KnowledgeBase, source_id: str, n: NormalizedIngredient) -> IngredientResolution:
    r = n.resolution
    concept = kb.concepts.get(r.concepts[0].slug) if r and r.concepts else None
    definite = [path for _, path in r.paths if path.certainty == "definite"] if r else []
    possible = [path for _, path in r.paths if path.certainty == "possible"] if r else []
    allergens = _dedupe([path.allergen_code for path in definite] + [t.allergen_code for t in r.terms]) if r else []
    return IngredientResolution(
        source_id=source_id,
        original_text=n.original,
        text=n.text,
        role=n.role,
        parent=n.parent,
        status=r.status if r else "class_name",
        match_method=n.method,
        match_confidence=n.confidence,
        corrections=n.corrections,
        suggestion=n.suggestion,
        concept=concept.slug if concept else None,
        concept_name=concept.name if concept else "",
        foodon_id=concept.foodon_id if concept else None,
        allergens=[a for a in allergens if a in kb.allergens],
        possible_allergens=_dedupe([path.allergen_code for path in possible if path.allergen_code not in allergens]),
        relationship_path=(definite or possible)[0].text if (definite or possible) else "",
    )
