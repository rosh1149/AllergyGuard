import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api } from "../../api/client";
import type { MenuItem, MenuItemDetail, MenuItemStatus, RecipeInput, VersionChanges } from "../../api/types";
import { useAllergens } from "../../components/useAllergens";
import { ErrorAlert, Field, formatDate, Spinner } from "../../components/ui";
import { useDash } from "./context";
import { CC_KINDS, CC_STATUS, cleanRecipe, emptyRecipe, RecipeEditor, recipeFromVersion } from "./RecipeEditor";

export function MenuPage() {
  const { rid, dash, canEdit } = useDash();
  const nav = useNavigate();
  const [params] = useSearchParams();
  const [branchId, setBranchId] = useState("");
  const [status, setStatus] = useState<"" | MenuItemStatus>("");
  const [items, setItems] = useState<MenuItem[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const onlyUnverified = params.get("filter") === "unverified";

  useEffect(() => {
    const q = new URLSearchParams();
    if (branchId) q.set("branch_id", branchId);
    if (status) q.set("status", status);
    setItems(null);
    api<MenuItem[]>(`/restaurants/${rid}/menu?${q}`).then(setItems).catch(setError);
  }, [rid, branchId, status]);

  const shown = items?.filter((i) => !onlyUnverified || i.current_version?.verification_status === "unverified");

  return (
    <div>
      <div className="page-header">
        <h1>Menu</h1>
        {canEdit && <Link to="/dashboard/menu/new"><button className="primary">+ Add dish</button></Link>}
      </div>
      <div className="row" style={{ marginBottom: "1rem" }}>
        {dash.branches.length > 1 && (
          <select aria-label="Branch" value={branchId} onChange={(e) => setBranchId(e.target.value)} style={{ maxWidth: 220 }}>
            <option value="">All branches</option>
            {dash.branches.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
          </select>
        )}
        <select aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value as MenuItemStatus | "")} style={{ maxWidth: 200 }}>
          <option value="">Current menu</option>
          <option value="available">Available</option>
          <option value="unavailable">Unavailable</option>
          <option value="archived">Archived</option>
        </select>
        {onlyUnverified && <span className="chip">Awaiting verification <Link to="/dashboard/menu" aria-label="Clear filter">×</Link></span>}
      </div>
      <ErrorAlert error={error} />
      {!shown && !error && <Spinner />}
      {shown && shown.length === 0 && <p className="muted">No dishes here yet.</p>}
      {shown && shown.length > 0 && (
        <div className="card table-wrap" style={{ padding: 0 }}>
          <table>
            <thead><tr><th>Dish</th><th>Branch</th><th>Status</th><th>Recipe</th><th>Attention</th></tr></thead>
            <tbody>
              {shown.map((i) => (
                <tr key={i.id} className="clickable" onClick={() => nav(`/dashboard/menu/${i.id}`)}>
                  <td><Link to={`/dashboard/menu/${i.id}`}><strong>{i.name}</strong></Link><br /><small className="muted">{i.category}</small></td>
                  <td>{i.branch_name}</td>
                  <td><span className={`badge ${i.status === "available" ? "ok" : "neutral"}`}>{i.status}</span></td>
                  <td>
                    {i.current_version ? (
                      <span className={`badge ${i.current_version.verification_status === "verified" ? "ok" : "o-POSSIBLE_CROSS_CONTACT"}`}>
                        v{i.current_version.version_number} {i.current_version.verification_status}
                      </span>
                    ) : "—"}
                  </td>
                  <td>
                    {(i.current_version?.warnings.length ?? 0) > 0 && <div><small className="muted">⚠ {i.current_version!.warnings.length} allergen warning(s)</small></div>}
                    {i.pending_feedback > 0 && <div><small className="muted">💬 {i.pending_feedback} suggestion(s)</small></div>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export function NewMenuItemPage() {
  const { rid, dash, reload, canEdit } = useDash();
  const nav = useNavigate();
  const [form, setForm] = useState({ name: "", category: "", description: "", branch_id: String(dash.branches[0]?.id ?? "") });
  const [recipe, setRecipe] = useState<RecipeInput>(emptyRecipe());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const item = await api<MenuItemDetail>(`/restaurants/${rid}/menu`, {
        method: "POST",
        json: { ...form, branch_id: Number(form.branch_id), recipe: { ...cleanRecipe(recipe), change_note: recipe.change_note || "Initial recipe" } },
      });
      reload();
      nav(`/dashboard/menu/${item.id}`);
    } catch (err) {
      setError(err);
      window.scrollTo({ top: 0 });
    } finally {
      setBusy(false);
    }
  };

  if (!canEdit) return <div className="alert info">Your role is read-only.</div>;
  return (
    <form onSubmit={submit} className="stack">
      <h1>Add a dish</h1>
      <ErrorAlert error={error} />
      <div className="card">
        <div className="grid-2">
          <Field label="Dish name"><input required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
          <Field label="Category"><input value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })} placeholder="e.g. Mains" /></Field>
        </div>
        {dash.branches.length > 1 && (
          <Field label="Branch">
            <select value={form.branch_id} onChange={(e) => setForm({ ...form, branch_id: e.target.value })}>
              {dash.branches.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
            </select>
          </Field>
        )}
        <Field label="Description"><textarea value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} style={{ minHeight: 70 }} /></Field>
      </div>
      <div className="card">
        <RecipeEditor value={recipe} onChange={setRecipe} canVerify showNote={false} />
      </div>
      <div className="row">
        <button className="primary" disabled={busy}>{busy ? "Saving…" : "Save dish"}</button>
        <Link to="/dashboard/menu"><button type="button">Cancel</button></Link>
      </div>
    </form>
  );
}

export function MenuItemPage() {
  const { rid, reload, canEdit } = useDash();
  const { byCode } = useAllergens();
  const { itemId } = useParams();
  const nav = useNavigate();
  const [item, setItem] = useState<MenuItemDetail | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [editing, setEditing] = useState(false);
  const [recipe, setRecipe] = useState<RecipeInput>(emptyRecipe());
  const [busy, setBusy] = useState(false);

  const load = () => api<MenuItemDetail>(`/restaurants/${rid}/menu/${itemId}`).then(setItem).catch(setError);
  useEffect(() => { load(); }, [rid, itemId]); // eslint-disable-line react-hooks/exhaustive-deps

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await load();
      reload();
    } catch (err) {
      setError(err);
      window.scrollTo({ top: 0 });
    } finally {
      setBusy(false);
    }
  };

  if (!item) return error ? <ErrorAlert error={error} /> : <Spinner />;
  const cur = item.current_version;
  const name = (c: string) => byCode[c]?.name ?? c;

  return (
    <div className="stack">
      <div className="page-header" style={{ marginBottom: 0 }}>
        <div>
          <Link to="/dashboard/menu">← Menu</Link>
          <h1 style={{ marginTop: "0.3rem" }}>{item.name}</h1>
          <p className="muted" style={{ margin: 0 }}>{item.category} · {item.branch_name}</p>
        </div>
        {canEdit && (
          <div className="row">
            <select aria-label="Availability" value={item.status} disabled={busy}
              onChange={(e) => act(() => api(`/restaurants/${rid}/menu/${item.id}`, { method: "PATCH", json: { status: e.target.value } }))}>
              <option value="available">Available</option>
              <option value="unavailable">Unavailable</option>
              <option value="archived">Archived</option>
            </select>
          </div>
        )}
      </div>
      <ErrorAlert error={error} />
      {item.pending_feedback > 0 && (
        <div className="alert warn">{item.pending_feedback} customer suggestion(s) for this dish. <Link to="/dashboard/feedback">Review them</Link></div>
      )}

      {editing ? (
        <form className="card" onSubmit={(e) => { e.preventDefault(); act(async () => {
          await api(`/restaurants/${rid}/menu/${item.id}/versions`, { method: "POST", json: cleanRecipe(recipe) });
          setEditing(false);
        }); }}>
          <h2>New recipe version</h2>
          <p className="muted">Saving creates version {(cur?.version_number ?? 0) + 1}. Earlier versions are kept.</p>
          <RecipeEditor value={recipe} onChange={setRecipe} canVerify={canEdit} />
          <div className="row" style={{ marginTop: "1rem" }}>
            <button className="primary" disabled={busy}>{busy ? "Saving…" : "Save new version"}</button>
            <button type="button" onClick={() => setEditing(false)}>Cancel</button>
          </div>
        </form>
      ) : cur && (
        <div className="card">
          <div className="spread">
            <h2 style={{ margin: 0 }}>Current recipe · v{cur.version_number}</h2>
            <div className="row">
              <span className={`badge ${cur.verification_status === "verified" ? "ok" : "o-POSSIBLE_CROSS_CONTACT"}`}>{cur.verification_status}</span>
              {canEdit && cur.verification_status === "unverified" && (
                <button className="small primary" disabled={busy} onClick={() => act(() => api(`/restaurants/${rid}/menu/${item.id}/versions/${cur.id}/verify`, { method: "POST" }))}>
                  Mark verified
                </button>
              )}
              {canEdit && <button className="small" onClick={() => { setRecipe(recipeFromVersion(cur)); setEditing(true); }}>Edit recipe</button>}
            </div>
          </div>
          {cur.warnings.length > 0 && (
            <div className="alert warn" style={{ marginTop: "0.8rem" }}>
              <strong>Check these allergens</strong>
              <ul style={{ margin: "0.3rem 0 0", paddingLeft: "1.1rem" }}>{cur.warnings.map((w, i) => <li key={i}>{w.message}</li>)}</ul>
            </div>
          )}
          <RecipeSummary v={cur} name={name} />
        </div>
      )}

      <details>
        <summary>Version history ({item.versions.length})</summary>
        <ul className="plain">
          {item.versions.map((v) => (
            <li key={v.id}>
              <div className="spread">
                <strong>v{v.version_number}{v.is_current && " (current)"}</strong>
                <span className={`badge ${v.verification_status === "verified" ? "ok" : "neutral"}`}>{v.verification_status}</span>
              </div>
              <small className="muted">
                {formatDate(v.created_at)}{v.created_by && ` by ${v.created_by}`}
                {v.verified_by && ` · verified by ${v.verified_by}`}
              </small>
              {v.change_note && <p style={{ margin: "0.2rem 0" }}>{v.change_note}</p>}
              <small>{v.ingredients.map((i) => i.name).join(", ")}</small>
              {v.changes && <VersionDiff c={v.changes} name={name} />}
            </li>
          ))}
        </ul>
      </details>
      {canEdit && item.status !== "archived" && (
        <p><button className="danger small" onClick={() => {
          if (confirm(`Archive ${item.name}? Customers will no longer see it. History is kept.`)) {
            act(() => api(`/restaurants/${rid}/menu/${item.id}`, { method: "DELETE" })).then(() => nav("/dashboard/menu"));
          }
        }}>Archive dish</button></p>
      )}
    </div>
  );
}

function RecipeSummary({ v, name }: { v: NonNullable<MenuItem["current_version"]>; name: (c: string) => string }) {
  return (
    <div style={{ marginTop: "0.8rem" }}>
      <table>
        <thead><tr><th>Ingredient</th><th>Contains</th></tr></thead>
        <tbody>
          {v.ingredients.map((i) => (
            <tr key={i.id}>
              <td>{i.name}{i.notes && <><br /><small className="muted">{i.notes}</small></>}</td>
              <td>{i.allergen_codes.length ? <div className="chips">{i.allergen_codes.map((c) => <span key={c} className="badge o-PRESENT">{name(c)}</span>)}</div> : <small className="muted">—</small>}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {v.dish_allergen_codes.length > 0 && (
        <p style={{ marginTop: "0.8rem" }}><strong>Also contains: </strong>{v.dish_allergen_codes.map(name).join(", ")}</p>
      )}
      <div style={{ marginTop: "0.8rem" }}>
        <strong>Cross-contact: </strong>
        {v.cross_contacts.length ? (
          <ul style={{ margin: "0.2rem 0 0", paddingLeft: "1.1rem" }}>
            {v.cross_contacts.map((c, i) => (
              <li key={i}>
                {name(c.allergen_code)}: {CC_KINDS[c.kind] ?? c.kind} ({c.level}){c.ingredient && ` · via ${c.ingredient}`}
                {c.description && ` · ${c.description}`} <small className="muted">· {CC_STATUS[c.status] ?? c.status}</small>
              </li>
            ))}
          </ul>
        ) : <span className="muted">none recorded</span>}
        <div>
          <small className={v.cross_contact_reviewed ? "ok-text" : "muted"}>
            {v.cross_contact_reviewed ? "Cross-contact reviewed by staff." : "Cross-contact not marked as reviewed: customers are told it wasn't assessed."}
          </small>
        </div>
      </div>
    </div>
  );
}

function VersionDiff({ c, name }: { c: VersionChanges; name: (c: string) => string }) {
  // "peanuts (shared_fryer)" -> "Peanuts (shared fryer or oil)"
  const cc = (s: string) => s.replace(/^(\S+) \((\w+)\)$/, (_, code: string, kind: string) =>
    `${name(code)} (${(CC_KINDS[kind as keyof typeof CC_KINDS] ?? kind).toLowerCase()})`);
  const parts: [string, string[], string][] = [
    ["Ingredients added", c.ingredients_added, "diff-add"],
    ["Ingredients removed", c.ingredients_removed, "diff-del"],
    ["Allergens added", c.allergens_added.map(name), "diff-add"],
    ["Allergens removed", c.allergens_removed.map(name), "diff-del"],
    ["Cross-contact added", c.cross_contact_added.map(cc), "diff-add"],
    ["Cross-contact removed", c.cross_contact_removed.map(cc), "diff-del"],
  ];
  const shown = parts.filter(([, v]) => v.length);
  if (!shown.length) return <div><small className="muted">No ingredient or allergen changes from v{c.previous_version}.</small></div>;
  return (
    <div style={{ marginTop: "0.2rem" }}>
      <small className="muted">Changes from v{c.previous_version}: </small>
      {shown.map(([label, v, cls]) => (
        <small key={label} style={{ marginRight: "0.6rem" }}>{label}: <span className={cls}>{v.join(", ")}</span></small>
      ))}
    </div>
  );
}
