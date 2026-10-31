import { useEffect, useState, type FormEvent } from "react";
import { api } from "../../api/client";
import type { PublicMenuItem, PublicRestaurant, RestaurantAnalysis } from "../../api/types";
import { PhotoPicker } from "../../components/PhotoPicker";
import { ResultView, StepsList } from "../../components/ResultView";
import { ErrorAlert, Field, Spinner } from "../../components/ui";
import { FeedbackForm } from "./FeedbackForm";

type Mode = "registered" | "unregistered";

export function RestaurantCheckPage() {
  const [mode, setMode] = useState<Mode>("registered");
  const [query, setQuery] = useState("");
  const [restaurants, setRestaurants] = useState<PublicRestaurant[]>([]);
  const [restaurant, setRestaurant] = useState<PublicRestaurant | null>(null);
  const [branchId, setBranchId] = useState<number | null>(null);
  const [menu, setMenu] = useState<PublicMenuItem[]>([]);
  const [menuItemId, setMenuItemId] = useState<number | null>(null);
  const [dishName, setDishName] = useState("");
  const [photos, setPhotos] = useState<File[]>([]);
  const [unreg, setUnreg] = useState({ restaurant_name: "", location: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [data, setData] = useState<RestaurantAnalysis | null>(null);
  const [showFeedback, setShowFeedback] = useState(false);

  useEffect(() => {
    if (mode !== "registered" || restaurant) return;
    const t = setTimeout(() => {
      api<PublicRestaurant[]>(`/public/restaurants?q=${encodeURIComponent(query)}`)
        .then(setRestaurants)
        .catch(setError);
    }, 250);
    return () => clearTimeout(t);
  }, [query, mode, restaurant]);

  useEffect(() => {
    setMenu([]);
    setMenuItemId(null);
    if (restaurant && branchId) {
      api<PublicMenuItem[]>(`/public/restaurants/${restaurant.id}/branches/${branchId}/menu`).then(setMenu).catch(setError);
    }
  }, [restaurant, branchId]);

  const chooseRestaurant = (r: PublicRestaurant) => {
    setRestaurant(r);
    setBranchId(r.branches.length === 1 ? r.branches[0].id : null);
    setData(null);
  };

  const run = async (overrideMenuItem?: number) => {
    const fd = new FormData();
    if (mode === "registered") {
      if (!restaurant || !branchId) {
        setError(new Error("Choose the restaurant and branch."));
        return;
      }
      fd.append("restaurant_id", String(restaurant.id));
      fd.append("branch_id", String(branchId));
      const item = overrideMenuItem ?? menuItemId;
      if (item) fd.append("menu_item_id", String(item));
    } else {
      if (!unreg.restaurant_name.trim()) {
        setError(new Error("Enter the restaurant's name."));
        return;
      }
      fd.append("restaurant_name", unreg.restaurant_name);
      fd.append("location", unreg.location);
    }
    if (!overrideMenuItem && !menuItemId && !dishName.trim() && !photos.length) {
      setError(new Error("Choose the dish from the menu, type its name, or add a photo."));
      return;
    }
    fd.append("dish_name", dishName.trim());
    if (!overrideMenuItem) photos.forEach((p) => fd.append("images", p));
    setBusy(true);
    setError(null);
    setData(null);
    setShowFeedback(false);
    try {
      setData(await api<RestaurantAnalysis>("/analyze/restaurant", { method: "POST", body: fd }));
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    run();
  };

  const branch = restaurant?.branches.find((b) => b.id === branchId);

  return (
    <div className="page narrow">
      <h1>Check restaurant food</h1>
      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={mode === "registered"} onClick={() => { setMode("registered"); setData(null); }}>
          Find a restaurant
        </button>
        <button role="tab" aria-selected={mode === "unregistered"} onClick={() => { setMode("unregistered"); setData(null); }}>
          Restaurant not listed
        </button>
      </div>

      <form className="card" onSubmit={submit}>
        {mode === "registered" ? (
          restaurant ? (
            <>
              <div className="spread" style={{ marginBottom: "0.9rem" }}>
                <div>
                  <strong>{restaurant.name}</strong>
                  {restaurant.cuisine && <span className="muted"> · {restaurant.cuisine}</span>}
                </div>
                <button type="button" className="small" onClick={() => { setRestaurant(null); setData(null); }}>Change</button>
              </div>
              {restaurant.branches.length > 1 && (
                <Field label="Branch">
                  <select required value={branchId ?? ""} onChange={(e) => setBranchId(Number(e.target.value) || null)}>
                    <option value="">Choose a branch…</option>
                    {restaurant.branches.map((b) => (
                      <option key={b.id} value={b.id}>{b.name}{b.city ? ` — ${b.city}` : ""}</option>
                    ))}
                  </select>
                </Field>
              )}
              {branch && menu.length > 0 && (
                <Field label="Dish from the menu" hint="Choosing from the menu gives the most reliable result.">
                  <select value={menuItemId ?? ""} onChange={(e) => setMenuItemId(Number(e.target.value) || null)}>
                    <option value="">Not sure / not listed</option>
                    {menu.map((m) => (
                      <option key={m.id} value={m.id}>{m.category ? `${m.category}: ` : ""}{m.name}</option>
                    ))}
                  </select>
                </Field>
              )}
            </>
          ) : (
            <>
              <Field label="Search restaurants">
                <input type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Restaurant name" />
              </Field>
              <ul className="plain">
                {restaurants.map((r) => (
                  <li key={r.id} className="spread">
                    <span>
                      <strong>{r.name}</strong>
                      <small className="muted"> {r.cuisine} · {r.branches.map((b) => b.city || b.name).join(", ")}</small>
                    </span>
                    <button type="button" className="small" onClick={() => chooseRestaurant(r)}>Select</button>
                  </li>
                ))}
                {restaurants.length === 0 && (
                  <li className="muted">
                    No registered restaurants found.{" "}
                    <button type="button" className="link" onClick={() => setMode("unregistered")}>Check an unlisted restaurant</button>
                  </li>
                )}
              </ul>
            </>
          )
        ) : (
          <>
            <div className="alert info">
              Without restaurant records we can only suggest what to ask. Public recipes show how a dish is usually made, not how this restaurant makes it.
            </div>
            <div className="grid-2">
              <Field label="Restaurant name"><input required value={unreg.restaurant_name} onChange={(e) => setUnreg({ ...unreg, restaurant_name: e.target.value })} /></Field>
              <Field label="Location (optional)"><input value={unreg.location} onChange={(e) => setUnreg({ ...unreg, location: e.target.value })} /></Field>
            </div>
          </>
        )}

        {(mode === "unregistered" || (restaurant && branchId && !menuItemId)) && (
          <>
            <Field label="Dish name">
              <input value={dishName} onChange={(e) => setDishName(e.target.value)} placeholder="e.g. Butter chicken" />
            </Field>
            <Field label="Dish photo (optional)">
              <PhotoPicker files={photos} onChange={setPhotos} />
            </Field>
          </>
        )}
        {(mode === "unregistered" || restaurant) && (
          <button className="primary block" disabled={busy}>{busy ? "Checking…" : "Check this dish"}</button>
        )}
      </form>

      <div style={{ marginTop: "1.25rem" }}>
        {busy && <Spinner label="Checking the restaurant's records…" />}
        <ErrorAlert error={error} />
        {data && (
          <div className="stack">
            {data.dish && (
              <div className="card">
                <div className="spread">
                  <div>
                    <strong>{data.dish.name}</strong>
                    <span className="muted"> at {data.restaurant.name}{data.restaurant.branch_name && ` (${data.restaurant.branch_name})`}</span>
                    <div>
                      <small className="muted">
                        Recipe version {data.dish.recipe_version} ·{" "}
                        {data.dish.verification_status === "verified" ? "verified by the restaurant" : "not yet verified by the restaurant"}
                      </small>
                    </div>
                  </div>
                  <span className={`badge ${data.dish.confident ? "ok" : "o-UNKNOWN"}`}>
                    {data.dish.confident ? "Dish confirmed" : "Dish not confirmed"}
                  </span>
                </div>
              </div>
            )}
            {data.candidates.length > 0 && (data.needs_confirmation || !data.dish || !data.dish.confident) && (
              <div className="alert warn">
                <strong>Is your dish one of these?</strong> Confirming it gives a reliable result.
                <div className="row" style={{ marginTop: "0.5rem" }}>
                  {data.candidates.map((c) => (
                    <button key={c.menu_item_id} className="small" onClick={() => { setMenuItemId(c.menu_item_id); run(c.menu_item_id); }}>
                      {c.name}
                    </button>
                  ))}
                </div>
              </div>
            )}
            <ResultView result={data.result} />
            {data.questions_for_staff.length > 0 && (
              <section className="card">
                <h3>Ask the staff</h3>
                <ul className="plain checklist">
                  {data.questions_for_staff.map((q, i) => <li key={i}>{q}</li>)}
                </ul>
              </section>
            )}
            {data.public_recipe_sources.length > 0 && (
              <details>
                <summary>Public recipes consulted (not this restaurant's)</summary>
                <ul>
                  {data.public_recipe_sources.map((s) => (
                    <li key={s.url}><a href={s.url} target="_blank" rel="noreferrer">{s.title}</a></li>
                  ))}
                </ul>
              </details>
            )}
            {data.can_submit_feedback && data.dish && (
              showFeedback ? (
                <FeedbackForm menuItemId={data.dish.menu_item_id} dishName={data.dish.name} onDone={() => setShowFeedback(false)} />
              ) : (
                <p>
                  Something wrong with this dish's information?{" "}
                  <button className="link" onClick={() => setShowFeedback(true)}>Suggest a correction</button>
                </p>
              )
            )}
            <StepsList steps={data.steps} />
          </div>
        )}
      </div>
    </div>
  );
}
