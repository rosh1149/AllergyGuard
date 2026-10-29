import { useState } from "react";
import { api } from "../../api/client";
import type { CrossContactLevel, RecipeInput, RecipeVersion } from "../../api/types";
import { useAllergens } from "../../components/useAllergens";
import { ErrorAlert, Field } from "../../components/ui";
import { useDash } from "./context";

export function emptyRecipe(): RecipeInput {
  return { ingredients: [{ name: "", notes: "", allergen_codes: [] }], dish_allergen_codes: [], cross_contacts: [], change_note: "", mark_verified: false };
}

export function recipeFromVersion(v: RecipeVersion | null): RecipeInput {
  if (!v) return emptyRecipe();
  return {
    ingredients: v.ingredients.map((i) => ({ name: i.name, notes: i.notes, allergen_codes: [...i.allergen_codes] })),
    dish_allergen_codes: [...v.dish_allergen_codes],
    cross_contacts: v.cross_contacts.map((c) => ({ ...c })),
    change_note: "",
    mark_verified: false,
  };
}

export function cleanRecipe(r: RecipeInput): RecipeInput {
  return {
    ...r,
    ingredients: r.ingredients.filter((i) => i.name.trim()).map((i) => ({ ...i, name: i.name.trim() })),
    cross_contacts: r.cross_contacts.filter((c) => c.allergen_code),
  };
}

function CodeToggles({ value, onChange, label }: { value: string[]; onChange: (v: string[]) => void; label: string }) {
  const { allergens } = useAllergens();
  return (
    <div className="code-select" role="group" aria-label={label}>
      {allergens.map((a) => {
        const on = value.includes(a.code);
        return (
          <button key={a.code} type="button" aria-pressed={on} onClick={() => onChange(on ? value.filter((c) => c !== a.code) : [...value, a.code])}>
            {a.name.replace("Cereals containing gluten", "Gluten").replace("Sulphur dioxide and sulphites", "Sulphites")}
          </button>
        );
      })}
    </div>
  );
}

export function RecipeEditor({
  value,
  onChange,
  canVerify,
  showNote = true,
}: {
  value: RecipeInput;
  onChange: (r: RecipeInput) => void;
  canVerify: boolean;
  showNote?: boolean;
}) {
  const { rid } = useDash();
  const { allergens } = useAllergens();
  const [checking, setChecking] = useState(false);
  const [checkMsg, setCheckMsg] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);

  const setIng = (i: number, patch: Partial<RecipeInput["ingredients"][number]>) =>
    onChange({ ...value, ingredients: value.ingredients.map((x, j) => (j === i ? { ...x, ...patch } : x)) });

  const suggest = async () => {
    const names = value.ingredients.map((i) => i.name.trim()).filter(Boolean);
    if (!names.length) return;
    setChecking(true);
    setError(null);
    try {
      const res = await api<{ name: string; allergen_codes: string[] }[]>(`/restaurants/${rid}/ingredients/check`, { method: "POST", json: { names } });
      const byName = Object.fromEntries(res.map((r) => [r.name, r.allergen_codes]));
      let added = 0;
      onChange({
        ...value,
        ingredients: value.ingredients.map((i) => {
          const extra = (byName[i.name.trim()] ?? []).filter((c) => !i.allergen_codes.includes(c));
          added += extra.length;
          return { ...i, allergen_codes: [...i.allergen_codes, ...extra] };
        }),
      });
      setCheckMsg(added ? `Added ${added} suggested allergen mark${added > 1 ? "s" : ""}. Please review them.` : "No additional allergens suggested.");
    } catch (err) {
      setError(err);
    } finally {
      setChecking(false);
    }
  };

  return (
    <div>
      <ErrorAlert error={error} />
      <div className="spread">
        <h3 style={{ margin: 0 }}>Ingredients</h3>
        <button type="button" className="small" onClick={suggest} disabled={checking}>
          {checking ? "Checking…" : "Suggest allergens from names"}
        </button>
      </div>
      {checkMsg && <small className="muted">{checkMsg}</small>}
      <p className="muted" style={{ fontSize: "0.88rem", margin: "0.3rem 0 0.5rem" }}>
        Mark every allergen each ingredient contains. Include sauces, marinades, oils and garnishes.
      </p>
      {value.ingredients.map((ing, i) => (
        <div className="ing-row" key={i}>
          <div>
            <input aria-label={`Ingredient ${i + 1} name`} placeholder="Ingredient" value={ing.name} onChange={(e) => setIng(i, { name: e.target.value })} />
            <input aria-label={`Ingredient ${i + 1} notes`} placeholder="Notes (optional)" value={ing.notes} onChange={(e) => setIng(i, { notes: e.target.value })} style={{ marginTop: "0.3rem", fontSize: "0.88rem" }} />
          </div>
          <CodeToggles label={`Allergens in ${ing.name || `ingredient ${i + 1}`}`} value={ing.allergen_codes} onChange={(c) => setIng(i, { allergen_codes: c })} />
          <button type="button" className="small danger" aria-label={`Remove ingredient ${i + 1}`} onClick={() => onChange({ ...value, ingredients: value.ingredients.filter((_, j) => j !== i) })}>
            Remove
          </button>
        </div>
      ))}
      <button type="button" className="small" style={{ marginTop: "0.5rem" }} onClick={() => onChange({ ...value, ingredients: [...value.ingredients, { name: "", notes: "", allergen_codes: [] }] })}>
        + Add ingredient
      </button>

      <h3 style={{ marginTop: "1.2rem" }}>Other allergens in the dish</h3>
      <p className="muted" style={{ fontSize: "0.88rem" }}>For allergens not tied to a listed ingredient (e.g. from a shared sauce base).</p>
      <CodeToggles label="Dish-level allergens" value={value.dish_allergen_codes} onChange={(c) => onChange({ ...value, dish_allergen_codes: c })} />

      <h3 style={{ marginTop: "1.2rem" }}>Cross-contact</h3>
      {value.cross_contacts.map((c, i) => (
        <div className="row" key={i} style={{ marginBottom: "0.4rem" }}>
          <select aria-label="Cross-contact allergen" value={c.allergen_code} style={{ maxWidth: 220 }}
            onChange={(e) => onChange({ ...value, cross_contacts: value.cross_contacts.map((x, j) => (j === i ? { ...x, allergen_code: e.target.value } : x)) })}>
            <option value="">Allergen…</option>
            {allergens.map((a) => <option key={a.code} value={a.code}>{a.name}</option>)}
          </select>
          <select aria-label="Cross-contact level" value={c.level} style={{ maxWidth: 140 }}
            onChange={(e) => onChange({ ...value, cross_contacts: value.cross_contacts.map((x, j) => (j === i ? { ...x, level: e.target.value as CrossContactLevel } : x)) })}>
            <option value="possible">Possible</option>
            <option value="known">Known</option>
          </select>
          <input aria-label="Cross-contact description" placeholder="e.g. shared fryer" value={c.description} style={{ flex: 1, minWidth: 160 }}
            onChange={(e) => onChange({ ...value, cross_contacts: value.cross_contacts.map((x, j) => (j === i ? { ...x, description: e.target.value } : x)) })} />
          <button type="button" className="small danger" onClick={() => onChange({ ...value, cross_contacts: value.cross_contacts.filter((_, j) => j !== i) })}>Remove</button>
        </div>
      ))}
      <button type="button" className="small" onClick={() => onChange({ ...value, cross_contacts: [...value.cross_contacts, { allergen_code: "", level: "possible", description: "" }] })}>
        + Add cross-contact
      </button>

      {showNote && (
        <div style={{ marginTop: "1.2rem" }}>
          <Field label="What changed?">
            <input value={value.change_note} onChange={(e) => onChange({ ...value, change_note: e.target.value })} placeholder="e.g. New supplier for the curry paste" />
          </Field>
        </div>
      )}
      {canVerify && (
        <label className="check" style={{ marginTop: "0.5rem" }}>
          <input type="checkbox" checked={value.mark_verified} onChange={(e) => onChange({ ...value, mark_verified: e.target.checked })} />
          I confirm these records are accurate (mark as verified)
        </label>
      )}
    </div>
  );
}
