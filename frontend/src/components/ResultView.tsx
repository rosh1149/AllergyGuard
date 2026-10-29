import type { AllergenAssessment, AnalysisResult, Outcome, Step } from "../api/types";
import { OUTCOME_LABELS, OutcomeBadge } from "./ui";

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

function AllergenCard({ a }: { a: AllergenAssessment }) {
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
    </div>
  );
}

export function ResultView({ result }: { result: AnalysisResult }) {
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
              <li key={i}>{c.description}</li>
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
            {result.sources.map((s) => (
              <li key={s.id}>
                <div className="spread">
                  <strong>{s.name}</strong>
                  <span className="badge neutral">{s.type_label}</span>
                </div>
                {s.url && (
                  <a href={s.url} target="_blank" rel="noreferrer">
                    View source
                  </a>
                )}
                {s.ingredients.length > 0 && (
                  <p style={{ margin: "0.3rem 0" }}>
                    <small>Ingredients: {s.ingredients.join(", ")}</small>
                  </p>
                )}
                {!s.used_for_absence && s.not_used_for_absence_reason && (
                  <small className="muted">Not used to rule allergens out: {s.not_used_for_absence_reason}</small>
                )}
              </li>
            ))}
          </ul>
        </details>
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
