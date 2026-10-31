import { useEffect, useState, type FormEvent } from "react";
import { api } from "../../api/client";
import type { AllergyProfile, Feedback } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { AllergenPicker, type AllergySelection } from "../../components/AllergenPicker";
import { ErrorAlert, Field, formatDate, Spinner } from "../../components/ui";

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
      <YourData />
    </div>
  );
}

function YourData() {
  const { logout } = useAuth();
  const [password, setPassword] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const download = async () => {
    setError(null);
    try {
      const data = await api<unknown>("/me/export");
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
      const a = document.createElement("a");
      a.href = url;
      a.download = "allergyguard-my-data.json";
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err);
    }
  };

  const remove = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api("/me", { method: "DELETE", json: { password } });
      logout();
    } catch (err) {
      setError(err);
      setBusy(false);
    }
  };

  return (
    <section className="card" style={{ marginTop: "1.5rem" }}>
      <h2>Your data</h2>
      <p className="muted">We store your allergies, your saved checks and suggestions you send to restaurants. Photos are never stored.</p>
      <ErrorAlert error={error} />
      <div className="row">
        <button onClick={download}>Download my data</button>
        <button className="danger" onClick={() => setDeleting(!deleting)}>Delete my account</button>
      </div>
      {deleting && (
        <form onSubmit={remove} style={{ marginTop: "1rem" }}>
          <p>This deletes your account, allergies and saved checks. Suggestions you already sent stay with the restaurant, without your name. This can't be undone.</p>
          <Field label="Enter your password to confirm">
            <input type="password" required value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
          </Field>
          <button className="danger" disabled={busy || !password}>{busy ? "Deleting…" : "Permanently delete my account"}</button>
        </form>
      )}
    </section>
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

const STATUS_LABEL: Record<string, string> = {
  unverified: "Awaiting review",
  clarification_requested: "Question for you",
  confirmed: "Confirmed",
  rejected: "Not accepted",
};

function ClarifyForm({ f, onDone }: { f: Feedback; onDone: () => void }) {
  const [answer, setAnswer] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const send = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api(`/me/feedback/${f.id}/clarification`, { method: "POST", json: { message: answer } });
      onDone();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };
  return (
    <form onSubmit={send} style={{ marginTop: "0.5rem" }}>
      <ErrorAlert error={error} />
      <Field label="Your answer">
        <textarea required value={answer} onChange={(e) => setAnswer(e.target.value)} style={{ minHeight: 70 }} />
      </Field>
      <button className="primary small" disabled={busy || !answer.trim()}>{busy ? "Sending…" : "Send answer"}</button>
    </form>
  );
}

export function MyFeedbackPage() {
  const [items, setItems] = useState<Feedback[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const load = () => {
    api<Feedback[]>("/me/feedback").then(setItems).catch(setError);
  };
  useEffect(load, []);
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
            <span className={`badge ${f.status === "confirmed" ? "ok" : f.status === "rejected" ? "neutral" : f.status === "clarification_requested" ? "o-POSSIBLE_CROSS_CONTACT" : "o-UNKNOWN"}`}>
              {STATUS_LABEL[f.status] ?? f.status}
            </span>
          </div>
          <p style={{ margin: "0.3rem 0" }}>
            {TYPE_LABELS[f.suggestion_type]}
            {f.ingredient_name && `: ${f.ingredient_name}`}
            {f.allergen_code && ` (${f.allergen_code})`}
          </p>
          {f.message && <p className="muted">"{f.message}"</p>}
          <small className="muted">Sent {formatDate(f.created_at)}</small>
          {f.clarification_request && (
            <div className="qa">
              <div><strong>The restaurant asks:</strong> {f.clarification_request}</div>
              {f.clarification_response && <div><strong>You answered:</strong> {f.clarification_response}</div>}
            </div>
          )}
          {f.status === "clarification_requested" && <ClarifyForm f={f} onDone={load} />}
          {f.decision_note && f.status !== "clarification_requested" && <p style={{ marginTop: "0.4rem" }}><small>Restaurant note: {f.decision_note}</small></p>}
          {f.resulting_version_number && <p style={{ margin: "0.3rem 0 0" }}><small className="ok-text">The restaurant updated its records (recipe v{f.resulting_version_number}).</small></p>}
        </div>
      ))}
    </div>
  );
}
