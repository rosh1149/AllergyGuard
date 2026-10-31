import { useState, type FormEvent } from "react";
import { api } from "../../api/client";
import type { SuggestionType } from "../../api/types";
import { useAllergens } from "../../components/useAllergens";
import { ErrorAlert, Field } from "../../components/ui";

const TYPES: { value: SuggestionType; label: string }[] = [
  { value: "missing_ingredient", label: "An ingredient is missing" },
  { value: "incorrect_ingredient", label: "An ingredient is listed but isn't used" },
  { value: "missing_allergen", label: "An allergen is missing" },
  { value: "incorrect_allergen", label: "An allergen is listed but isn't in the dish" },
  { value: "outdated_recipe", label: "The dish has changed; the records are out of date" },
  { value: "cross_contact", label: "There's a cross-contact risk" },
  { value: "other", label: "Something else" },
];

export function FeedbackForm({ menuItemId, dishName, onDone }: { menuItemId: number; dishName: string; onDone: () => void }) {
  const { allergens } = useAllergens();
  const [type, setType] = useState<SuggestionType>("missing_ingredient");
  const [ingredient, setIngredient] = useState("");
  const [allergen, setAllergen] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [sent, setSent] = useState(false);

  const needsIngredient = type === "missing_ingredient" || type === "incorrect_ingredient";
  const needsAllergen = type === "missing_allergen" || type === "incorrect_allergen" || type === "cross_contact";
  const needsMessage = type === "other" || type === "outdated_recipe";

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api("/feedback", {
        method: "POST",
        json: {
          menu_item_id: menuItemId,
          suggestion_type: type,
          ingredient_name: needsIngredient ? ingredient : "",
          allergen_code: needsAllergen || allergen ? allergen || null : null,
          message,
        },
      });
      setSent(true);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  if (sent) {
    return (
      <div className="alert success">
        Thanks. Your suggestion was sent to the restaurant. It stays marked as unverified until their staff review it.{" "}
        <button className="link" onClick={onDone}>Close</button>
      </div>
    );
  }

  return (
    <form className="card" onSubmit={submit}>
      <h3>Suggest a correction for {dishName}</h3>
      <p className="muted">The restaurant reviews suggestions before changing its records.</p>
      <ErrorAlert error={error} />
      <Field label="What's wrong?">
        <select value={type} onChange={(e) => setType(e.target.value as SuggestionType)}>
          {TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
        </select>
      </Field>
      {needsIngredient && (
        <Field label="Ingredient"><input required value={ingredient} onChange={(e) => setIngredient(e.target.value)} /></Field>
      )}
      {needsAllergen && (
        <Field label="Allergen">
          <select required value={allergen} onChange={(e) => setAllergen(e.target.value)}>
            <option value="">Choose…</option>
            {allergens.map((a) => <option key={a.code} value={a.code}>{a.name}</option>)}
          </select>
        </Field>
      )}
      <Field label={needsMessage ? "Details" : "Details (optional)"} hint={type === "outdated_recipe" ? "What has changed?" : undefined}>
        <textarea required={needsMessage} value={message} onChange={(e) => setMessage(e.target.value)} />
      </Field>
      <div className="row">
        <button className="primary" disabled={busy}>{busy ? "Sending…" : "Send suggestion"}</button>
        <button type="button" onClick={onDone}>Cancel</button>
      </div>
    </form>
  );
}
