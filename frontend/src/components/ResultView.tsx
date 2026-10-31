import { Link } from "react-router-dom";
import type { AllergenAssessment, AnalysisResult, EvidenceRef, Outcome, SourceSummary, StageRecord, Step } from "../api/types";
import { formatDate, OUTCOME_LABELS, OutcomeBadge } from "./ui";

const HEADLINES: Record<Outcome, string> = {
  PRESENT: "Contains one or more of your allergens",
  POSSIBLE_CROSS_CONTACT: "Possible cross-contact with your allergens",
  UNKNOWN: "Not enough reliable information",
  NOT_LISTED_IN_VERIFIED_RECORDS: "Your allergens were not found in the verified records",
};

const SUBLINES: Record<Outcome, string> = {
  PRESENT: "Don't rely on this item unless staff or the manufacturer confirm an allergen-free option.",
  POSSIBLE_CROSS_CONTACT: "The item may come into contact with an allergen during preparation or manufacturing.",
  UNKNOWN: "Some of your allergens couldn't be assessed. Treat this as unconfirmed.",
  NOT_LISTED_IN_VERIFIED_RECORDS:
    "This is not a guarantee that they are absent. Records can be incomplete or out of date.",
};

export const BASIS_LABELS: Record<string, string> = {
  direct_ingredient: "Listed ingredient",
  derived_ingredient: "Made from an allergen source",
  declared_by_source: "Declared by the source",
  cross_contact: "Cross-contact",
  possible_source: "Ingredient source not stated",
  unverified_source: "Unverified source only",
  free_from_claim: "Free-from claim (can't be verified)",
  not_found_in_verified_records: "Checked against verified records",
  insufficient_evidence: "Not enough reliable information",
};

export const RULE_LABELS: Record<string, string> = {
  "R1.present_ingredient": "An ingredient in a trusted source indicates this allergen.",
  "R1.present_declared": "A trusted source declares this allergen.",
  "R2.cross_contact_declared": "A trusted source reports cross-contact.",
  "R2.cross_contact_candidate": "An unverified or uncertain source reports cross-contact.",
  "R3.unverified_candidate": "Only an unverified or possibly mismatched source suggests it.",
  "R3.possible_source": "An ingredient may come from this allergen; its source isn't stated.",
  "R4.free_from_claim": "Free-from claims can't be verified, so they never rule an allergen out.",
  "R5.not_listed_verified": "Complete, current, verified sources were checked and don't list it.",
  "R6.absence_not_supported": "No sign of it, but the information isn't good enough to rule it out.",
  "B.no_eligible_source": "No source was complete, verified, current, identified and fully readable.",
  "B.list_mismatch": "The ingredient lists from different sources disagree.",
};

const VERIFICATION: Record<string, string> = {
  label: "Package label",
  verified: "Verified by restaurant staff",
  unverified: "Not yet verified by staff",
  community: "Community database",
  public: "Public web recipe",
  customer: "Customer report (unverified)",
};

const COMPLETENESS: Record<string, string> = {
  complete: "Complete",
  partial: "Partial",
  unavailable: "No ingredient list",
  conflicting: "Conflicts with another source",
};

const METHODS: Record<string, string> = {
  spelling: "spelling corrected",
  ocr_correction: "photo misread corrected",
  fuzzy: "closest known name",
  qualified: "source named on label",
};

function dateOnly(iso?: string | null) {
  return iso ? new Date(iso).toLocaleDateString(undefined, { dateStyle: "medium" }) : "";
}

function EvidenceLine({ e }: { e: EvidenceRef }) {
  const what = e.ingredient || e.detail || e.matched_term;
  return (
    <li>
      <span>{what && <strong>{what}</strong>}</span>
      {e.matched_as && <small> read as “{e.matched_as}”{e.match_method && METHODS[e.match_method] ? ` (${METHODS[e.match_method]})` : ""}</small>}
      {e.relationship_path && <div><small className="muted">Link: {e.relationship_path}</small></div>}
      <div>
        <small className="muted">
          {e.source_reference || e.source_name}
          {e.source_verification && ` · ${VERIFICATION[e.source_verification] ?? e.source_verification}`}
          {e.source_as_of && ` · as of ${dateOnly(e.source_as_of)}`}
          {e.match_confidence != null && e.match_confidence < 1 && ` · match confidence ${Math.round(e.match_confidence * 100)}%`}
        </small>
      </div>
    </li>
  );
}

function AllergenCard({ a }: { a: AllergenAssessment }) {
  const rules = a.rules ?? [];
  return (
    <div className={`allergen-card o-${a.outcome}`}>
      <div className="spread">
        <strong>
          {a.name}
          {a.is_custom && <small className="muted"> (your custom allergy)</small>}
        </strong>
        <OutcomeBadge outcome={a.outcome} />
      </div>
      <ul>
        {a.reasons.map((r, i) => (
          <li key={i}>{r}</li>
        ))}
      </ul>
      {(a.evidence.length > 0 || rules.length > 0) && (
        <details className="inner">
          <summary>Why: evidence and rules</summary>
          {a.basis && a.basis.length > 0 && (
            <div className="chips" style={{ marginBottom: "0.4rem" }}>
              {[...new Set(a.basis)].map((b) => <span key={b} className="chip">{BASIS_LABELS[b] ?? b}</span>)}
            </div>
          )}
          {a.evidence.length > 0 && <ul className="plain evidence">{a.evidence.map((e, i) => <EvidenceLine key={i} e={e} />)}</ul>}
          {rules.length > 0 && (
            <div style={{ marginTop: "0.4rem" }}>
              <small className="muted">Decision rule{rules.length > 1 ? "s" : ""}:</small>
              <ul className="rules">
                {rules.map((r) => <li key={r}><code>{r}</code> {RULE_LABELS[r] ?? ""}</li>)}
              </ul>
            </div>
          )}
          {a.regulated_in && a.regulated_in.length > 0 && (
            <small className="muted">Allergen labelling rules list this in: {a.regulated_in.join(", ")}</small>
          )}
        </details>
      )}
    </div>
  );
}

function SourceItem({ s }: { s: SourceSummary }) {
  return (
    <li>
      <div className="spread">
        <strong>{s.name}</strong>
        <span className="row" style={{ gap: "0.3rem" }}>
          <span className="badge neutral">{s.type_label}</span>
          {s.completeness && s.completeness !== "complete" && <span className="badge o-UNKNOWN">{COMPLETENESS[s.completeness]}</span>}
          {s.fresh === false && <span className="badge o-POSSIBLE_CROSS_CONTACT">Out of date</span>}
        </span>
      </div>
      <small className="muted">
        {[s.reference, s.verification && (VERIFICATION[s.verification] ?? s.verification),
          s.as_of && `as of ${dateOnly(s.as_of)}${s.age_days != null ? ` (${s.age_days} days ago)` : ""}`]
          .filter(Boolean).join(" · ")}
      </small>
      {s.url && (
        <div><a href={s.url} target="_blank" rel="noreferrer">View source</a></div>
      )}
      {s.ingredients.length > 0 && (
        <p style={{ margin: "0.3rem 0" }}>
          <small>Ingredients: {s.ingredients.join(", ")}</small>
        </p>
      )}
      {s.unresolved_items && s.unresolved_items.length > 0 && (
        <p style={{ margin: "0.2rem 0" }}><small>Not identified: {s.unresolved_items.join(", ")}</small></p>
      )}
      {s.used_for_absence ? (
        <small className="ok-text">Used to check which allergens are not listed.</small>
      ) : s.not_used_for_absence_reason ? (
        <small className="muted">Not used to rule allergens out: {s.not_used_for_absence_reason}</small>
      ) : null}
    </li>
  );
}

const STAGE_NAMES: Record<string, string> = {
  barcode: "Barcode", ocr: "Label reading (OCR)", image_recognition: "Product recognition", product_lookup: "Product lookup",
  dish_recognition: "Dish recognition", dish_match: "Menu match", restaurant_records: "Restaurant records",
  public_recipes: "Public recipes", normalization: "Ingredient normalization", concept_mapping: "Concept mapping",
  fusion: "Evidence fusion", decision: "Decision", explanation: "Explanation", analysis: "Analysis",
};

export function TraceView({ trace, duration, knowledgeVersion }: { trace: StageRecord[]; duration?: number | null; knowledgeVersion?: number }) {
  return (
    <details>
      <summary>Processing details{duration != null && <small className="muted"> · {duration} ms</small>}</summary>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Stage</th><th>Status</th><th>Time</th><th>Counts</th></tr></thead>
          <tbody>
            {trace.map((t, i) => (
              <tr key={i}>
                <td>{STAGE_NAMES[t.name] ?? t.name.replace(/_/g, " ")}</td>
                <td><span className={`badge ${t.status === "done" ? "ok" : t.status === "failed" ? "o-PRESENT" : "neutral"}`}>{t.status}</span></td>
                <td style={{ whiteSpace: "nowrap" }}>{t.duration_ms != null ? `${t.duration_ms} ms` : ""}</td>
                <td><small className="muted">{Object.entries(t.counts ?? {}).map(([k, v]) => `${k.replace(/_/g, " ")}: ${v}`).join(" · ")}</small></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {knowledgeVersion != null && <small className="muted">Allergen knowledge base version {knowledgeVersion}. These details contain no personal information.</small>}
    </details>
  );
}

export function ResultView({ result, saved = true }: { result: AnalysisResult; saved?: boolean }) {
  const o = result.overall_outcome;
  return (
    <div className="stack" aria-live="polite">
      <div className={`verdict o-${o}`}>
        <small style={{ fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em" }}>
          {OUTCOME_LABELS[o]}
        </small>
        <h2>{HEADLINES[o]}</h2>
        <p>{SUBLINES[o]}</p>
        {result.identity_label && (
          <p style={{ marginTop: "0.5rem" }}>
            <small>Checked: {result.identity_label}</small>
          </p>
        )}
        {saved && result.assessment_id && (
          <p style={{ marginTop: "0.25rem" }}>
            <small>Saved to <Link to={`/history/${result.assessment_id}`}>your history</Link>{result.evaluated_at && ` · ${formatDate(result.evaluated_at)}`}</small>
          </p>
        )}
      </div>

      <section>
        <h3>Your allergies</h3>
        <div className="allergen-list">
          {result.your_allergens.map((a) => (
            <AllergenCard key={a.code} a={a} />
          ))}
        </div>
      </section>

      {result.other_allergens.length > 0 && (
        <section>
          <h3>Other allergens found</h3>
          <div className="chips">
            {result.other_allergens.map((a) => (
              <span key={a.code} className={`badge o-${a.outcome}`}>
                {a.name}: {a.outcome_label.toLowerCase()}
              </span>
            ))}
          </div>
        </section>
      )}

      {result.conflicts.length > 0 && (
        <div className="alert warn">
          <strong>Conflicting information</strong>
          <ul style={{ margin: "0.3rem 0 0", paddingLeft: "1.1rem" }}>
            {result.conflicts.map((c, i) => (
              <li key={c.id || i}>
                {c.description}
                {c.resolution && <div><small>How it was handled: {c.resolution}</small></div>}
              </li>
            ))}
          </ul>
        </div>
      )}

      {result.limitations.length > 0 && (
        <section className="card">
          <h3>Limitations</h3>
          <ul style={{ margin: 0, paddingLeft: "1.1rem" }}>
            {result.limitations.map((l, i) => (
              <li key={i}>{l}</li>
            ))}
          </ul>
        </section>
      )}

      <details>
        <summary>Explanation {result.explanation_source === "llm" && <small className="muted">(written by AI from the findings above)</small>}</summary>
        <div className="explanation">{result.explanation}</div>
      </details>

      {result.sources.length > 0 && (
        <details>
          <summary>Evidence sources ({result.sources.length})</summary>
          <ul className="plain">
            {result.sources.map((s) => <SourceItem key={s.id} s={s} />)}
          </ul>
        </details>
      )}

      {result.trace && result.trace.length > 0 && (
        <TraceView trace={result.trace} duration={result.duration_ms} knowledgeVersion={result.knowledge_version} />
      )}

      <p className="disclaimer">{result.disclaimer}</p>
    </div>
  );
}

export function StepsList({ steps }: { steps: Step[] }) {
  return (
    <details>
      <summary>How this was checked</summary>
      <ul className="plain">
        {steps.map((s, i) => (
          <li key={i} className="spread">
            <span>
              <strong style={{ textTransform: "capitalize" }}>{s.name.replace(/_/g, " ")}</strong>
              {s.detail && <small className="muted"> — {s.detail}</small>}
            </span>
            <span className={`badge ${s.status === "done" ? "ok" : s.status === "failed" ? "o-PRESENT" : "neutral"}`}>
              {s.status}
            </span>
          </li>
        ))}
      </ul>
    </details>
  );
}
