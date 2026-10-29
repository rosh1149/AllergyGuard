"""Deterministic allergen assessment.

Rules (per allergen, first match wins):
  1. Any presence evidence from an authoritative source whose identity is trusted -> PRESENT
  2. Any cross-contact evidence (declared or "may contain")                         -> POSSIBLE_CROSS_CONTACT
  3. Presence evidence only from candidate/untrusted sources                         -> UNKNOWN
  4. A "free-from" claim is the only thing mentioning it                             -> UNKNOWN
  5. At least one complete, reliable, verified ingredient source and no
     ingredient-list mismatch between such sources                                   -> NOT_LISTED_IN_VERIFIED_RECORDS
  6. Otherwise                                                                       -> UNKNOWN

Missing evidence never produces a "safe" outcome; NOT_LISTED only means "not found in the
verified records that were available".
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from itertools import combinations

from app.engine.knowledge import KnowledgeBase, custom_match
from app.engine.text import (
    free_from_claims,
    is_nonspecific,
    normalize,
    split_items,
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


def _prepare(src: EvidenceSource, ctx: AnalysisContext) -> _Prepared:
    items: list[str] = []
    precautions: list[str] = []
    for raw in src.ingredients:
        t = normalize(raw)
        if t and t not in items:
            items.append(t)
    if src.ingredients_text:
        body, stmts = split_precautions(normalize(src.ingredients_text))
        precautions.extend(stmts)
        items.extend(i for i in split_items(body) if i not in items)
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
    return _Prepared(src, items, precautions, trusted, absence_ok=not reason, absence_reason=reason)


def _ref(p: _Prepared, kind: str, ingredient: str = "", term: str = "", detail: str = "") -> EvidenceRef:
    return EvidenceRef(
        source_id=p.src.id,
        source_name=p.src.name,
        source_type=p.src.type,
        kind=kind,
        ingredient=ingredient,
        matched_term=term,
        detail=detail,
    )


def _collect(
    kb: KnowledgeBase, prepared: list[_Prepared], custom_terms: list[str]
) -> tuple[dict[str, list[EvidenceRef]], list[str]]:
    """Evidence per allergen key ("<code>" or "custom:<term>"), plus non-specific ingredients."""
    ev: dict[str, list[EvidenceRef]] = defaultdict(list)
    nonspecific: list[str] = []

    for p in prepared:
        presence_kind = "presence" if p.trusted else "candidate"
        for item in p.items:
            claims = free_from_claims(item)
            claim_codes: set[str] = set()
            for c in claims:
                claim_codes |= kb.codes_in(c)
            for code in claim_codes:
                ev[code].append(_ref(p, "claim", item, detail=f"'{item}' claims to be free of it"))
            for m in kb.match(item):
                if m.allergen_code in claim_codes:
                    continue
                ev[m.allergen_code].append(_ref(p, presence_kind, item, m.term))
                if p.trusted:
                    p.present_codes.add(m.allergen_code)
            for term in custom_terms:
                key = f"custom:{term}"
                if any(term in c for c in claims):
                    ev[key].append(_ref(p, "claim", item, detail=f"'{item}' claims to be free of it"))
                elif custom_match(term, item):
                    ev[key].append(_ref(p, presence_kind, item, term))
            if is_nonspecific(item) and item not in nonspecific:
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
    return ev, nonspecific


def _conflicts(kb: KnowledgeBase, prepared: list[_Prepared]) -> tuple[list[Conflict], bool]:
    conflicts: list[Conflict] = []

    # A source's own allergen declaration omits an allergen its ingredients indicate.
    for p in prepared:
        if p.src.declared_allergens is None or not p.trusted:
            continue
        from_items = {m.allergen_code for i in p.items for m in kb.match(i)}
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

    present = by_kind["presence"] + by_kind["declared"]
    if present:
        reasons = []
        for r in present:
            if r.kind == "declared":
                reasons.append(f"{name} is declared as an allergen ({where(r)}).")
            else:
                reasons.append(f"Ingredient '{r.ingredient}' indicates {name} ({where(r)}).")
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

    if by_kind["candidate"]:
        reasons = [
            f"'{r.ingredient or r.detail}' suggests {name} may be present, but the source is not verified "
            f"or may not match this item ({where(r)})."
            for r in by_kind["candidate"]
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
    prepared = [_prepare(s, ctx) for s in sources]
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
    )
