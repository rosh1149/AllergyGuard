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

import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from itertools import combinations

from app.engine.knowledge import KnowledgeBase, custom_match
from app.engine.rules import rules_for
from app.engine.normalize import NormalizedIngredient, normalize_ingredients
from app.engine.text import (
    is_nonspecific,
    free_from_claims,
    normalize,
    split_precautions,
)
from app.engine.types import (
    StageRecord,
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
    keys: dict[str, str] = field(default_factory=dict)  # item text -> what it is (concept slugs, else its text)
    allergen_items: set[str] = field(default_factory=set)  # items carrying any (definite or possible) allergen
    normalized: list[NormalizedIngredient] = field(default_factory=list)
    collected_at: datetime | None = None
    age_days: int | None = None
    fresh: bool = True
    unresolved: list[str] = field(default_factory=list)
    incomplete: list[str] = field(default_factory=list)


CROSS_CONTACT_KINDS = {
    "shared_equipment": "shared equipment", "shared_fryer": "shared fryer", "preparation_area": "shared preparation area",
    "utensils": "shared utensils", "storage": "shared storage", "manufacturing_facility": "same manufacturing facility",
    "supplier": "supplier information", "precautionary_label": "precautionary label statement", "other": "",
}


def _age_days(src: EvidenceSource, now: datetime) -> int | None:
    if src.as_of is None:
        return None
    as_of = src.as_of if src.as_of.tzinfo else src.as_of.replace(tzinfo=timezone.utc)
    return max(0, (now - as_of).days)


def _prepare(kb: KnowledgeBase, src: EvidenceSource, ctx: AnalysisContext, now: datetime) -> _Prepared:
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

    age = _age_days(src, now)
    fresh = src.max_age_days is None or age is None or age <= src.max_age_days
    unresolved = list(dict.fromkeys(
        n.original for n in normalized if n.counts and n.resolution is not None and n.resolution.status == "unresolved"))
    incomplete = list(dict.fromkeys(
        (f"'{n.original}': " if n.original else "") + n.incomplete for n in normalized if n.incomplete))

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
    elif not fresh:
        reason = f"The information is {age} days old (more than {src.max_age_days}) and may be out of date."
    elif unresolved or incomplete:
        parts = []
        if unresolved:
            parts.append("It contains ingredient text that could not be identified: " + ", ".join(unresolved) + ".")
        if incomplete:
            parts.append("The ingredient list looks incomplete: " + "; ".join(incomplete) + ".")
        reason = " ".join(parts)
    keys, allergen_items = {}, set()
    for n in normalized:
        if n.counts and n.text in items:
            r = n.resolution
            keys[n.text] = "+".join(sorted({c.slug for c in r.concepts})) if r and r.concepts and r.status != "unresolved" else n.text
            if r and (r.terms or r.paths):
                allergen_items.add(n.text)
    return _Prepared(src, items, precautions, trusted, absence_ok=not reason, absence_reason=reason, normalized=normalized,
                     keys=keys, allergen_items=allergen_items,
                     collected_at=now, age_days=age, fresh=fresh, unresolved=unresolved, incomplete=incomplete)


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
    nodes: tuple[str, ...] = (),
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
        path_nodes=list(nodes),
        matched_as=n.text if n is not None and n.text != n.original else "",
        match_method=n.method if n is not None else "",
        match_confidence=n.confidence if n is not None else None,
        source_reference=p.src.reference,
        source_verification=p.src.verification,
        source_as_of=p.src.as_of,
        collected_at=p.collected_at,
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
                    ev[code].append(_ref(p, presence_kind, shown, cm.alias, concept=cm.slug, path=path.text, certainty="definite", n=n,
                                        nodes=path.nodes))
                    if p.trusted:
                        p.present_codes.add(code)
                else:
                    ev[code].append(_ref(p, "possible_source", shown, cm.alias, concept=cm.slug, path=path.text, certainty="possible", n=n,
                                        nodes=path.nodes))
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
            general = res.status == "ambiguous_source" or (res.status == "possible_allergen" and is_nonspecific(item))
            if general and n.role != "compound" and item not in nonspecific:
                nonspecific.append(item)

        for code in p.src.declared_allergens or []:
            if code in kb.allergens:
                ev[code].append(_ref(p, "declared" if p.trusted else "candidate", detail="Declared by the source"))
                if p.trusted:
                    p.present_codes.add(code)

        cc_kind = "cross_contact" if p.trusted else "candidate_cross_contact"
        for decl in p.src.cross_contact:
            if decl.allergen_code in kb.allergens:
                qualifiers = [q for q in (CROSS_CONTACT_KINDS.get(decl.kind, decl.kind), decl.status if decl.status != "verified" else "") if q]
                detail = (f"{decl.level.capitalize()} cross-contact" + (f" ({', '.join(qualifiers)})" if qualifiers else "")
                          + (f" through the ingredient '{decl.ingredient}'" if decl.ingredient else "")
                          + (f": {decl.description}" if decl.description else ""))
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


def _dated(p: _Prepared) -> str:
    return f"{p.src.name} (as of {p.src.as_of.date().isoformat()})" if p.src.as_of else p.src.name


def _newer(a: _Prepared, b: _Prepared) -> str:
    """A sentence naming the more recent source, when both are dated and differ."""
    if a.src.as_of and b.src.as_of and a.src.as_of.date() != b.src.as_of.date():
        newer = a if _age_days(a.src, a.collected_at) < _age_days(b.src, b.collected_at) else b
        return f" {newer.src.name} is the more recent source."
    return ""


def _conflicts(kb: KnowledgeBase, prepared: list[_Prepared], ev: dict[str, list[EvidenceRef]]) -> tuple[list[Conflict], bool]:
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
            conflicts.append(Conflict(
                kind="declaration_mismatch",
                allergen_code=code,
                description=(f"{p.src.name} lists an ingredient that indicates {kb.name(code)}, "
                             f"but its allergen declaration does not include {kb.name(code)}."),
                source_ids=[p.src.id],
                resolution=f"The ingredient is used: {kb.name(code)} is reported as present.",
                handling="cautious_finding_kept",
            ))

    # Sources trusted enough to say what is in the item (stale ones included: their presence still counts).
    comparable = [p for p in prepared if p.trusted and p.items and p.src.complete]
    list_mismatch = False
    for a, b in combinations(comparable, 2):
        for code in sorted(a.present_codes ^ b.present_codes):
            has, lacks = (a, b) if code in a.present_codes else (b, a)
            conflicts.append(Conflict(
                kind="allergen_disagreement",
                allergen_code=code,
                description=f"{_dated(has)} indicates {kb.name(code)}, but {_dated(lacks)} does not mention it.",
                source_ids=[has.src.id, lacks.src.id],
                resolution=f"The more cautious finding is kept: {kb.name(code)} is reported as present." + _newer(a, b),
                handling="cautious_finding_kept",
            ))
        # Compare what the ingredients ARE (matched concepts), not how they are written: "wholegrain oat
        # flakes" and "oat flakes" are the same ingredient.
        ka, kb_ = set(a.keys.values()), set(b.keys.values())
        only = {a.src.id: sorted(t for t, k in a.keys.items() if k not in kb_),
                b.src.id: sorted(t for t, k in b.keys.items() if k not in ka)}
        only = {sid: items for sid, items in only.items() if items}
        if not only:
            continue
        overlap = len(ka & kb_) / max(1, min(len(ka), len(kb_)))
        if len(ka) >= 3 and len(kb_) >= 3 and overlap < LIST_MISMATCH_THRESHOLD:
            list_mismatch = True
            conflicts.append(Conflict(
                kind="ingredient_list_mismatch",
                description=(f"The ingredient lists from {_dated(a)} and {_dated(b)} differ substantially "
                             f"({overlap:.0%} of ingredients in common). They may describe different products, "
                             "variants or recipe versions."),
                source_ids=[a.src.id, b.src.id],
                resolution="Neither list is used to rule allergens out; anything either list indicates is kept." + _newer(a, b),
                handling="absence_blocked", status="unresolved", only_in=only,
            ))
        else:
            carries = [t for p_ in (a, b) for t in only.get(p_.src.id, []) if t in p_.allergen_items]
            listed = "; ".join(f"only {next(q.src.name for q in (a, b) if q.src.id == sid)} lists {', '.join(items)}"
                               for sid, items in only.items())
            conflicts.append(Conflict(
                kind="ingredient_difference",
                description=f"The ingredient lists from {_dated(a)} and {_dated(b)} differ slightly: {listed}.",
                source_ids=[a.src.id, b.src.id],
                resolution=("The differing ingredients carry no allergen link, so the difference does not change any "
                            "allergen finding." if not carries else
                            "Every allergen either list indicates is kept.") + _newer(a, b),
                handling="no_allergen_effect" if not carries else "cautious_finding_kept",
                only_in=only,
            ))

    # Cross-contact: one source records it, another trusted source says it assessed cross-contact and found none.
    for code, refs in ev.items():
        if code not in kb.allergens:
            continue
        flagged = {r.source_id for r in refs if r.kind == "cross_contact"}
        for p in prepared:
            if flagged and p.trusted and p.src.cross_contact_assessed and p.src.id not in flagged:
                others = [q for q in prepared if q.src.id in flagged]
                conflicts.append(Conflict(
                    kind="cross_contact_disagreement",
                    allergen_code=code,
                    description=(f"{', '.join(_dated(q) for q in others)} records possible cross-contact with {kb.name(code)}, "
                                 f"but {_dated(p)} records none."),
                    source_ids=[*sorted(flagged), p.src.id],
                    resolution="The more cautious finding is kept: possible cross-contact is reported.",
                    handling="cross_contact_kept",
                ))

    for i, c in enumerate(conflicts, 1):
        c.id = f"c{i}"
        if c.allergen_code:
            for r in ev.get(c.allergen_code, []):
                if r.source_id in c.source_ids:
                    r.conflict_ids.append(c.id)
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
    now = ctx.now or datetime.now(timezone.utc)
    custom_terms = [normalize(t) for t in profile.custom_terms if normalize(t)]
    t0 = time.perf_counter()
    prepared = [_prepare(kb, s, ctx, now) for s in sources]
    t1 = time.perf_counter()
    evidence, nonspecific = _collect(kb, prepared, custom_terms)
    t2 = time.perf_counter()
    conflicts, list_mismatch = _conflicts(kb, prepared, evidence)
    for code, refs in evidence.items():  # complete each finding's provenance
        for r in refs:
            r.allergen_category = kb.name(code) if code in kb.allergens else code.removeprefix("custom:")
            if r.concept and r.concept in kb.concepts:
                r.foodon_id = kb.concepts[r.concept].foodon_id
    t3 = time.perf_counter()

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

    assessed_by = [p for p in prepared if p.trusted and p.src.cross_contact_assessed]

    def cross_contact_status(refs: list[EvidenceRef]) -> str:
        if any(r.kind in ("cross_contact", "candidate_cross_contact") for r in refs):
            return "reported"
        return "none_reported" if assessed_by else "not_assessed"

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
            basis=_basis(outcome, evidence.get(code, [])),
            rules=rules_for(outcome, evidence.get(code, []), bool(eligible), list_mismatch),
            cross_contact=cross_contact_status(evidence.get(code, [])),
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
            basis=_basis(outcome, evidence.get(key, [])),
            rules=rules_for(outcome, evidence.get(key, []), bool(eligible), list_mismatch),
            cross_contact=cross_contact_status(evidence.get(key, [])),
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
    for p in prepared:
        if not p.fresh and p.age_days is not None:
            limitations.append(f"{p.src.name} was last updated or verified {p.age_days} days ago; it was not used to rule allergens out.")
        if p.src.cross_contact_assessed is False:
            limitations.append(f"{p.src.name} does not record a cross-contact review. No cross-contact record is not evidence of no cross-contact.")
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
    incomplete_all = _dedupe([x for p in prepared for x in p.incomplete])
    if incomplete_all:
        limitations.append("Part of the ingredient list looks incomplete: " + "; ".join(incomplete_all) + ".")
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
            reference=p.src.reference,
            verification=p.src.verification,
            as_of=p.src.as_of,
            age_days=p.age_days,
            fresh=p.fresh,
            completeness=_completeness(p, conflicts),
            unresolved_items=p.unresolved,
        )
        for p in prepared
    ]

    t4 = time.perf_counter()
    trace = list(ctx.stages) + _engine_stages(prepared, conflicts, eligible, list_mismatch, yours,
                                              (t0, t1, t2, t3, t4), ctx.identity_confidence)
    return AnalysisResult(
        trace=trace,
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
        evaluated_at=now,
    )


def _ms(a: float, b: float) -> int:
    return round((b - a) * 1000)


def _engine_stages(prepared, conflicts, eligible, list_mismatch, yours, t, identity_confidence) -> list[StageRecord]:
    """Counts only: no ingredient text, no allergy profile."""
    items = [n for p in prepared for n in p.normalized if n.counts]
    methods = Counter(n.method for n in items)
    statuses = Counter(n.resolution.status for n in items if n.resolution is not None)
    rules = Counter(r for a in yours for r in a.rules)
    return [
        StageRecord(name="normalization", duration_ms=_ms(t[0], t[1]),
                    counts={"sources": len(prepared), "items": len(items),
                            **{f"method_{k}": v for k, v in sorted(methods.items())}}),
        StageRecord(name="concept_mapping", duration_ms=_ms(t[1], t[2]),
                    counts={f"status_{k}": v for k, v in sorted(statuses.items())}),
        StageRecord(name="fusion", duration_ms=_ms(t[2], t[3]),
                    counts={"eligible_for_absence": len(eligible), "list_mismatch": int(list_mismatch),
                            **({"identity_confidence": identity_confidence} if identity_confidence is not None else {}),
                            "conflicts": len(conflicts),
                            **{f"conflict_{k}": v for k, v in sorted(Counter(c.kind for c in conflicts).items())}}),
        StageRecord(name="decision", duration_ms=_ms(t[3], t[4]),
                    counts={"allergens_checked": len(yours), **{f"rule_{k}": v for k, v in sorted(rules.items())}}),
    ]


BASIS = {
    "declared": "declared_by_source",
    "cross_contact": "cross_contact",
    "candidate_cross_contact": "cross_contact",
    "possible_source": "possible_source",
    "candidate": "unverified_source",
    "claim": "free_from_claim",
}


def _basis(outcome: Outcome, refs: list[EvidenceRef]) -> list[str]:
    """What kinds of evidence the outcome rests on, for the explanation."""
    out = []
    for r in refs:
        if r.kind == "presence":
            out.append("derived_ingredient" if "," in r.relationship_path else "direct_ingredient")
        elif r.kind in BASIS:
            out.append(BASIS[r.kind])
    if outcome == Outcome.NOT_LISTED_IN_VERIFIED_RECORDS:
        out.append("not_found_in_verified_records")
    elif outcome == Outcome.UNKNOWN and not refs:
        out.append("insufficient_evidence")
    return _dedupe(out)


def _completeness(p: _Prepared, conflicts: list[Conflict]) -> str:
    if not p.items:
        return "unavailable"
    if any(c.kind == "ingredient_list_mismatch" and p.src.id in c.source_ids for c in conflicts):
        return "conflicting"
    if not p.src.complete or p.unresolved or p.incomplete:
        return "partial"
    return "complete"


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
        status=r.status if r else ("list_note" if n.role == "list_note" else "class_name"),
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
        candidates=n.candidates,
        unresolved_reasons=n.unresolved_reasons,
        interpretations=r.interpretations if r else [],
        incomplete=n.incomplete,
    )
