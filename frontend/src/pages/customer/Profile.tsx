import { useEffect, useState } from "react";
import { api } from "../../api/client";
import type { AllergyProfile, Feedback } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { AllergenPicker, type AllergySelection } from "../../components/AllergenPicker";
import { ErrorAlert, formatDate, Spinner } from "../../components/ui";

export function ProfilePage() {
  const { user, refresh } = useAuth();
  const [sel, setSel] = useState<AllergySelection>({ allergen_codes: [], custom_allergies: [] });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    const p = user?.allergy_profile;
    if (p) setSel({ allergen_codes: p.allergens.map((a) => a.code), custom_allergies: p.custom_allergies });
  }, [user]);

  const save = async () => {
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      await api<AllergyProfile>("/me/allergy-profile", { method: "PUT", json: sel });
      await refresh();
      setSaved(true);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page narrow">
      <h1>Your allergies</h1>
      <p className="muted">Every check you run uses this list.</p>
      <div className="card">
        <ErrorAlert error={error} />
        {saved && <div className="alert success">Saved. Future checks will use your updated allergies.</div>}
        <AllergenPicker value={sel} onChange={(v) => { setSel(v); setSaved(false); }} />
        <button className="primary" style={{ marginTop: "1rem" }} onClick={save}
          disabled={busy || (sel.allergen_codes.length === 0 && sel.custom_allergies.length === 0)}>
          {busy ? "Saving…" : "Save allergies"}
        </button>
      </div>
      <p className="muted" style={{ marginTop: "1rem" }}>Signed in as {user?.email}</p>
    </div>
  );
}

const TYPE_LABELS: Record<string, string> = {
  missing_ingredient: "Missing ingredient",
  incorrect_ingredient: "Incorrect ingredient",
  missing_allergen: "Missing allergen",
  incorrect_allergen: "Incorrect allergen",
  outdated_recipe: "Outdated recipe",
  cross_contact: "Cross-contact",
  other: "Other",
};

export function MyFeedbackPage() {
  const [items, setItems] = useState<Feedback[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    api<Feedback[]>("/me/feedback").then(setItems).catch(setError);
  }, []);
  return (
    <div className="page narrow">
      <h1>Your suggestions</h1>
      <ErrorAlert error={error} />
      {!items && !error && <Spinner />}
      {items?.length === 0 && <p className="muted">You haven't suggested any corrections yet.</p>}
      {items?.map((f) => (
        <div className="card" key={f.id}>
          <div className="spread">
            <strong>{f.menu_item_name}</strong>
            <span className={`badge ${f.status === "confirmed" ? "ok" : f.status === "rejected" ? "neutral" : "o-UNKNOWN"}`}>
              {f.status === "unverified" ? "Awaiting review" : f.status}
            </span>
          </div>
          <p style={{ margin: "0.3rem 0" }}>
            {TYPE_LABELS[f.suggestion_type]}
            {f.ingredient_name && `: ${f.ingredient_name}`}
            {f.allergen_code && ` (${f.allergen_code})`}
          </p>
          {f.message && <p className="muted">"{f.message}"</p>}
          <small className="muted">Sent {formatDate(f.created_at)}</small>
          {f.decision_note && <p style={{ marginTop: "0.4rem" }}><small>Restaurant note: {f.decision_note}</small></p>}
        </div>
      ))}
    </div>
  );
}
