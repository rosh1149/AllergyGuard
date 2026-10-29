import { useState, type FormEvent } from "react";
import { api } from "../../api/client";
import type { AnalysisResult } from "../../api/types";
import { ResultView } from "../../components/ResultView";
import { ErrorAlert, Field, Spinner } from "../../components/ui";

export function TextCheckPage() {
  const [form, setForm] = useState({ product_name: "", ingredients_text: "", may_contain_text: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [result, setResult] = useState<AnalysisResult | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(await api<AnalysisResult>("/analyze/text", { method: "POST", json: form }));
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page narrow">
      <h1>Check an ingredient list</h1>
      <form className="card" onSubmit={submit}>
        <Field label="Product name (optional)">
          <input value={form.product_name} onChange={(e) => setForm({ ...form, product_name: e.target.value })} />
        </Field>
        <Field label="Ingredients" hint="Copy the full list exactly as printed, including anything in brackets.">
          <textarea required minLength={2} value={form.ingredients_text} onChange={(e) => setForm({ ...form, ingredients_text: e.target.value })} placeholder="Ingredients: wheat flour, sugar, butter (milk), salt…" />
        </Field>
        <Field label="Allergy advice / may contain (optional)">
          <input value={form.may_contain_text} onChange={(e) => setForm({ ...form, may_contain_text: e.target.value })} placeholder="May contain nuts." />
        </Field>
        <button className="primary block" disabled={busy}>{busy ? "Checking…" : "Check ingredients"}</button>
      </form>
      <div style={{ marginTop: "1.25rem" }}>
        {busy && <Spinner label="Checking…" />}
        <ErrorAlert error={error} />
        {result && <ResultView result={result} />}
      </div>
    </div>
  );
}
