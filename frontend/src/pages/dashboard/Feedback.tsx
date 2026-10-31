import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import type { Feedback, SuggestionStatus } from "../../api/types";
import { useAllergens } from "../../components/useAllergens";
import { ErrorAlert, formatDate, Spinner } from "../../components/ui";
import { useDash } from "./context";

const TYPE_LABELS: Record<string, string> = {
  missing_ingredient: "Missing ingredient",
  incorrect_ingredient: "Ingredient not used",
  missing_allergen: "Missing allergen",
  incorrect_allergen: "Incorrect allergen",
  outdated_recipe: "Outdated recipe",
  cross_contact: "Cross-contact risk",
  other: "Other",
};

const EFFECT: Record<string, string> = {
  missing_ingredient: "Confirming adds this ingredient (with its allergens) in a new verified recipe version.",
  incorrect_ingredient: "Confirming removes this ingredient in a new verified recipe version.",
  missing_allergen: "Confirming declares this allergen for the dish in a new verified recipe version.",
  cross_contact: "Confirming records a possible cross-contact in a new verified recipe version.",
  incorrect_allergen: "Confirming records your decision only. Removing an allergen is never automatic: edit the recipe yourself if it is wrong.",
  outdated_recipe: "Confirming marks the current recipe as unverified, so it can't rule allergens out until you update and verify it.",
  other: "Confirming records your decision; edit the recipe yourself if something needs to change.",
};

const TABS: [SuggestionStatus, string][] = [
  ["unverified", "To review"],
  ["clarification_requested", "Waiting for customer"],
  ["confirmed", "Confirmed"],
  ["rejected", "Rejected"],
];

const EVENT_LABEL: Record<string, string> = {
  submitted: "Submitted", clarification_requested: "Question sent", clarified: "Customer answered",
  confirmed: "Confirmed", rejected: "Rejected",
};

export function FeedbackPage() {
  const { rid, reload, canEdit } = useDash();
  const { byCode } = useAllergens();
  const [tab, setTab] = useState<SuggestionStatus>("unverified");
  const [items, setItems] = useState<Feedback[] | null>(null);
  const [notes, setNotes] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState<number | null>(null);
  const [error, setError] = useState<unknown>(null);

  const load = () => {
    setItems(null);
    api<Feedback[]>(`/restaurants/${rid}/feedback?status=${tab}`).then(setItems).catch(setError);
  };
  useEffect(load, [rid, tab]); // eslint-disable-line react-hooks/exhaustive-deps

  const decide = async (id: number, decision: "confirmed" | "rejected" | "clarification_requested") => {
    if (decision === "clarification_requested" && !(notes[id] ?? "").trim()) {
      setError(new Error("Type your question for the customer in the note box first."));
      return;
    }
    setBusy(id);
    setError(null);
    try {
      await api(`/restaurants/${rid}/feedback/${id}/decision`, { method: "POST", json: { decision, note: notes[id] ?? "" } });
      load();
      reload();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div>
      <h1>Customer feedback</h1>
      <p className="muted">Suggestions never change your records until an owner or manager confirms them.</p>
      <div className="tabs" role="tablist">
        {TABS.map(([t, label]) => (
          <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}>{label}</button>
        ))}
      </div>
      <ErrorAlert error={error} />
      {!items && !error && <Spinner />}
      {items?.length === 0 && <p className="muted">Nothing here.</p>}
      {items?.map((f) => (
        <div className="card" key={f.id}>
          <div className="spread">
            <div>
              <Link to={`/dashboard/menu/${f.menu_item_id}`}><strong>{f.menu_item_name}</strong></Link>
              <small className="muted"> · recipe v{f.recipe_version_number} · {formatDate(f.created_at)}</small>
            </div>
            <span className="badge neutral">{TYPE_LABELS[f.suggestion_type]}</span>
          </div>
          <p style={{ margin: "0.5rem 0" }}>
            {f.ingredient_name && <><strong>Ingredient:</strong> {f.ingredient_name} </>}
            {f.allergen_code && <><strong>Allergen:</strong> {byCode[f.allergen_code]?.name ?? f.allergen_code}</>}
          </p>
          {f.message && <p className="muted">"{f.message}"</p>}
          {f.clarification_request && (
            <div className="qa">
              <div><strong>You asked:</strong> {f.clarification_request}</div>
              <div>{f.clarification_response ? <><strong>Customer:</strong> {f.clarification_response}</> : <small className="muted">No answer yet.</small>}</div>
            </div>
          )}
          {(f.status === "unverified" || f.status === "clarification_requested") ? (
            canEdit ? (
              <>
                <small className="muted">{EFFECT[f.suggestion_type]}</small>
                <div className="row" style={{ marginTop: "0.5rem" }}>
                  <input aria-label="Decision note" placeholder="Note, or your question for the customer" value={notes[f.id] ?? ""} onChange={(e) => setNotes({ ...notes, [f.id]: e.target.value })} style={{ flex: 1, minWidth: 180 }} />
                  <button className="primary small" disabled={busy === f.id} onClick={() => decide(f.id, "confirmed")}>Confirm</button>
                  <button className="small" disabled={busy === f.id} onClick={() => decide(f.id, "rejected")}>Reject</button>
                  {f.status === "unverified" && (
                    <button className="small" disabled={busy === f.id} onClick={() => decide(f.id, "clarification_requested")}>Ask customer</button>
                  )}
                </div>
              </>
            ) : <small className="muted">An owner or manager needs to review this.</small>
          ) : (
            <small className="muted">
              {f.status} {formatDate(f.decided_at)}
              {f.resulting_version_number && ` · created recipe v${f.resulting_version_number}`}
              {f.decision_note && ` · "${f.decision_note}"`}
            </small>
          )}
          {f.impact && (
            <p style={{ margin: "0.4rem 0 0" }}>
              <small>
                <strong>Impact:</strong> recipe v{f.impact.resulting_version_number}
                {f.impact.version_is_current ? " (current)" : " (since replaced)"} · used in {f.impact.assessments_since} customer check{f.impact.assessments_since === 1 ? "" : "s"}
                {f.impact.assessments_flagging_allergen != null && `, ${f.impact.assessments_flagging_allergen} of which warned about this allergen`}
              </small>
            </p>
          )}
          {f.history && f.history.length > 1 && (
            <details className="inner">
              <summary>History</summary>
              <ul className="timeline">
                {f.history.map((h, i) => (
                  <li key={i}>{EVENT_LABEL[h.action] ?? h.action} · {formatDate(h.at)}{h.note && ` · "${h.note}"`}</li>
                ))}
              </ul>
            </details>
          )}
        </div>
      ))}
    </div>
  );
}
