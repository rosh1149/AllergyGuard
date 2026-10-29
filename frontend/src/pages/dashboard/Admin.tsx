import { useEffect, useState, type FormEvent } from "react";
import { api } from "../../api/client";
import type { AuditEntry, Branch, Member, MembershipRole, Restaurant } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { ErrorAlert, Field, formatDate, Spinner } from "../../components/ui";
import { useDash } from "./context";

export function BranchesPage() {
  const { rid, dash, reload, isOwner } = useDash();
  const [form, setForm] = useState({ name: "", address: "", city: "" });
  const [error, setError] = useState<unknown>(null);

  const call = async (fn: () => Promise<unknown>) => {
    setError(null);
    try {
      await fn();
      reload();
    } catch (err) {
      setError(err);
    }
  };

  return (
    <div className="stack">
      <h1>Branches</h1>
      <ErrorAlert error={error} />
      <div className="card">
        <ul className="plain">
          {dash.branches.map((b: Branch) => (
            <li key={b.id} className="spread">
              <span><strong>{b.name}</strong> <small className="muted">{[b.address, b.city].filter(Boolean).join(", ")}</small></span>
              <span className="row">
                <span className={`badge ${b.is_active ? "ok" : "neutral"}`}>{b.is_active ? "Active" : "Inactive"}</span>
                {isOwner && (
                  <button className="small" onClick={() => call(() => api(`/restaurants/${rid}/branches/${b.id}`, { method: "PATCH", json: { is_active: !b.is_active } }))}>
                    {b.is_active ? "Deactivate" : "Activate"}
                  </button>
                )}
              </span>
            </li>
          ))}
        </ul>
      </div>
      {isOwner && (
        <form className="card" onSubmit={(e: FormEvent) => { e.preventDefault(); call(async () => {
          await api(`/restaurants/${rid}/branches`, { method: "POST", json: form });
          setForm({ name: "", address: "", city: "" });
        }); }}>
          <h2>Add a branch</h2>
          <div className="grid-2">
            <Field label="Name"><input required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
            <Field label="City"><input value={form.city} onChange={(e) => setForm({ ...form, city: e.target.value })} /></Field>
          </div>
          <Field label="Address"><input value={form.address} onChange={(e) => setForm({ ...form, address: e.target.value })} /></Field>
          <button className="primary">Add branch</button>
        </form>
      )}
    </div>
  );
}

export function TeamPage() {
  const { rid, reload, isOwner } = useDash();
  const { user } = useAuth();
  const [members, setMembers] = useState<Member[] | null>(null);
  const [form, setForm] = useState({ email: "", full_name: "", role: "staff" as MembershipRole, initial_password: "" });
  const [error, setError] = useState<unknown>(null);
  const load = () => api<Member[]>(`/restaurants/${rid}/team`).then(setMembers).catch(setError);
  useEffect(() => { load(); }, [rid]); // eslint-disable-line react-hooks/exhaustive-deps

  const call = async (fn: () => Promise<unknown>) => {
    setError(null);
    try {
      await fn();
      await load();
      reload();
    } catch (err) {
      setError(err);
    }
  };

  return (
    <div className="stack">
      <h1>Team</h1>
      <p className="muted">Owners manage everything. Managers edit menus and recipes and review feedback. Staff can view only.</p>
      <ErrorAlert error={error} />
      {!members ? <Spinner /> : (
        <div className="card table-wrap" style={{ padding: 0 }}>
          <table>
            <thead><tr><th>Name</th><th>Email</th><th>Role</th><th /></tr></thead>
            <tbody>
              {members.map((m) => (
                <tr key={m.user_id}>
                  <td>{m.full_name}{m.user_id === user?.id && <small className="muted"> (you)</small>}</td>
                  <td>{m.email}</td>
                  <td>
                    {isOwner ? (
                      <select aria-label={`Role for ${m.full_name}`} value={m.role} onChange={(e) => call(() => api(`/restaurants/${rid}/team/${m.user_id}`, { method: "PATCH", json: { role: e.target.value } }))}>
                        <option value="owner">Owner</option><option value="manager">Manager</option><option value="staff">Staff</option>
                      </select>
                    ) : m.role}
                  </td>
                  <td>{isOwner && m.user_id !== user?.id && (
                    <button className="small danger" onClick={() => confirm(`Remove ${m.full_name} from the team?`) && call(() => api(`/restaurants/${rid}/team/${m.user_id}`, { method: "DELETE" }))}>Remove</button>
                  )}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {isOwner && (
        <form className="card" onSubmit={(e: FormEvent) => { e.preventDefault(); call(async () => {
          await api(`/restaurants/${rid}/team`, { method: "POST", json: { ...form, initial_password: form.initial_password || null } });
          setForm({ email: "", full_name: "", role: "staff", initial_password: "" });
        }); }}>
          <h2>Add a team member</h2>
          <div className="grid-2">
            <Field label="Email"><input type="email" required value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></Field>
            <Field label="Name"><input value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} /></Field>
            <Field label="Role">
              <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as MembershipRole })}>
                <option value="staff">Staff (view only)</option><option value="manager">Manager</option><option value="owner">Owner</option>
              </select>
            </Field>
            <Field label="Initial password" hint="Needed if this person doesn't have an AllergyGuard account yet.">
              <input type="password" minLength={8} value={form.initial_password} onChange={(e) => setForm({ ...form, initial_password: e.target.value })} />
            </Field>
          </div>
          <button className="primary">Add member</button>
        </form>
      )}
    </div>
  );
}

export function SettingsPage() {
  const { rid, dash, reload, isOwner } = useDash();
  const [form, setForm] = useState({ name: dash.restaurant.name, cuisine: dash.restaurant.cuisine, description: dash.restaurant.description });
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);
  return (
    <form className="stack" onSubmit={async (e) => {
      e.preventDefault();
      setError(null);
      setSaved(false);
      try {
        await api<Restaurant>(`/restaurants/${rid}`, { method: "PATCH", json: form });
        reload();
        setSaved(true);
      } catch (err) {
        setError(err);
      }
    }}>
      <h1>Settings</h1>
      <ErrorAlert error={error} />
      {saved && <div className="alert success">Saved.</div>}
      <div className="card">
        <fieldset disabled={!isOwner} style={{ border: "none", padding: 0, margin: 0 }}>
          <Field label="Restaurant name"><input required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
          <Field label="Cuisine"><input value={form.cuisine} onChange={(e) => setForm({ ...form, cuisine: e.target.value })} /></Field>
          <Field label="Description"><textarea value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></Field>
          {isOwner ? <button className="primary">Save settings</button> : <small className="muted">Only owners can change settings.</small>}
        </fieldset>
      </div>
    </form>
  );
}

export function AuditPage() {
  const { rid } = useDash();
  const [rows, setRows] = useState<AuditEntry[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => { api<AuditEntry[]>(`/restaurants/${rid}/audit?limit=200`).then(setRows).catch(setError); }, [rid]);
  return (
    <div>
      <h1>Audit log</h1>
      <p className="muted">Every change to trusted records and every feedback decision.</p>
      <ErrorAlert error={error} />
      {!rows && !error && <Spinner />}
      {rows && (
        <div className="card table-wrap" style={{ padding: 0 }}>
          <table>
            <thead><tr><th>When</th><th>Action</th><th>Details</th></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id}>
                  <td style={{ whiteSpace: "nowrap" }}>{formatDate(r.created_at)}</td>
                  <td><span className="mono">{r.action}</span><br /><small className="muted">{r.entity_type} #{r.entity_id}</small></td>
                  <td><small className="mono">{Object.entries(r.details).filter(([, v]) => v !== null && v !== "").map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`).join(" · ")}</small></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
