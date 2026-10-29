import { useState } from "react";
import { useAllergens } from "./useAllergens";

export interface AllergySelection {
  allergen_codes: string[];
  custom_allergies: string[];
}

export function AllergenPicker({
  value,
  onChange,
}: {
  value: AllergySelection;
  onChange: (v: AllergySelection) => void;
}) {
  const { allergens } = useAllergens();
  const [custom, setCustom] = useState("");

  const toggle = (code: string) =>
    onChange({
      ...value,
      allergen_codes: value.allergen_codes.includes(code)
        ? value.allergen_codes.filter((c) => c !== code)
        : [...value.allergen_codes, code],
    });

  const addCustom = () => {
    const term = custom.trim().toLowerCase();
    if (term && !value.custom_allergies.includes(term)) {
      onChange({ ...value, custom_allergies: [...value.custom_allergies, term] });
    }
    setCustom("");
  };

  return (
    <div>
      <div className="checkgrid" role="group" aria-label="Allergens">
        {allergens.map((a) => (
          <label key={a.code} className="check" title={a.description}>
            <input type="checkbox" checked={value.allergen_codes.includes(a.code)} onChange={() => toggle(a.code)} />
            {a.name}
          </label>
        ))}
      </div>
      <div style={{ marginTop: "0.9rem" }}>
        <span style={{ fontWeight: 600, fontSize: "0.92rem" }}>Other allergies</span>
        <div className="row" style={{ marginTop: "0.3rem" }}>
          <input
            value={custom}
            placeholder="e.g. kiwi"
            aria-label="Add another allergy"
            onChange={(e) => setCustom(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                addCustom();
              }
            }}
            style={{ flex: 1, minWidth: 160 }}
          />
          <button type="button" onClick={addCustom}>
            Add
          </button>
        </div>
        {value.custom_allergies.length > 0 && (
          <div className="chips" style={{ marginTop: "0.5rem" }}>
            {value.custom_allergies.map((t) => (
              <span className="chip" key={t}>
                {t}
                <button
                  type="button"
                  aria-label={`Remove ${t}`}
                  onClick={() =>
                    onChange({ ...value, custom_allergies: value.custom_allergies.filter((x) => x !== t) })
                  }
                >
                  ×
                </button>
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
