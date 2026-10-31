import { useState, type FormEvent } from "react";
import { api } from "../../api/client";
import type { PackagedAnalysis } from "../../api/types";
import { PhotoPicker } from "../../components/PhotoPicker";
import { ResultView, StepsList } from "../../components/ResultView";
import { ErrorAlert, Field, Spinner } from "../../components/ui";

export function PackagedCheckPage() {
  const [photos, setPhotos] = useState<File[]>([]);
  const [barcode, setBarcode] = useState("");
  const [productName, setProductName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [data, setData] = useState<PackagedAnalysis | null>(null);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    run(barcode);
  };

  const run = async (code: string) => {
    if (!photos.length && !code.trim() && !productName.trim()) {
      setError(new Error("Add a photo, or enter a barcode or product name."));
      return;
    }
    const fd = new FormData();
    photos.forEach((p) => fd.append("images", p));
    fd.append("barcode", code.trim());
    fd.append("product_name", productName.trim());
    setBusy(true);
    setError(null);
    setData(null);
    try {
      setData(await api<PackagedAnalysis>("/analyze/packaged", { method: "POST", body: fd }));
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page narrow">
      <h1>Check packaged food</h1>
      <form className="card" onSubmit={submit}>
        <Field label="Photos" hint="Best results: one photo of the front and one clear photo of the ingredients panel.">
          <PhotoPicker files={photos} onChange={setPhotos} />
        </Field>
        <div className="grid-2">
          <Field label="Barcode (optional)">
            <input inputMode="numeric" value={barcode} onChange={(e) => setBarcode(e.target.value)} placeholder="e.g. 5000159407236" />
          </Field>
          <Field label="Product name (optional)">
            <input value={productName} onChange={(e) => setProductName(e.target.value)} placeholder="Brand and product" />
          </Field>
        </div>
        <button className="primary block" disabled={busy}>{busy ? "Checking…" : "Check this product"}</button>
      </form>

      <div style={{ marginTop: "1.25rem" }}>
        {busy && <Spinner label="Reading the label and looking up the product…" />}
        <ErrorAlert error={error} />
        {data && (
          <div className="stack">
            {data.product && (
              <div className="card">
                <div className="spread">
                  <div>
                    <strong>{[data.product.brand, data.product.name].filter(Boolean).join(" ") || data.product.barcode}</strong>
                    {data.product.quantity && <span className="muted"> · {data.product.quantity}</span>}
                    <div>
                      <small className="muted">
                        Identified by {data.product.identification_method.replace(/_/g, " ")}
                        {data.product.barcode && ` · barcode ${data.product.barcode}`}
                      </small>
                    </div>
                  </div>
                  <span className={`badge ${data.product.confident ? "ok" : "o-UNKNOWN"}`}>
                    {data.product.confident ? "Confident match" : "Possible match"}
                  </span>
                </div>
              </div>
            )}
            {data.needs_confirmation && data.product_candidates && data.product_candidates.length > 0 && (
              <div className="card">
                <h3>Which product is it?</h3>
                <p className="muted">Several products match. Until you choose, the product database isn't treated as reliable.</p>
                <div className="row">
                  {data.product_candidates.map((c) => (
                    <button key={c.barcode} className="small" onClick={() => { setBarcode(c.barcode); run(c.barcode); }}>
                      {c.name} <small className="muted">({c.barcode})</small>
                    </button>
                  ))}
                </div>
              </div>
            )}
            <ResultView result={data.result} />
            {data.label.ingredients_found && (
              <details>
                <summary>Ingredient text read from your photo</summary>
                <p className="mono">{data.label.ingredients_text}</p>
                <small className="muted">Reading confidence: {Math.round(data.label.confidence * 100)}%</small>
              </details>
            )}
            <StepsList steps={data.steps} />
          </div>
        )}
      </div>
    </div>
  );
}
