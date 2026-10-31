import { Fragment, useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { api } from "../../api/client";
import type { AuditEntry, Metrics, SourceAdapterInfo, UnresolvedIngredient } from "../../api/types";
import { RULE_LABELS } from "../../components/ResultView";
import { ReviewQueue } from "../../components/ReviewQueue";
import { ErrorAlert, formatDate, OUTCOME_LABELS, Spinner } from "../../components/ui";
import type { Outcome } from "../../api/types";

export function AdminLayout() {
  return (
    <div className="dash">
      <nav className="sidebar" aria-label="Administration">
        <div className="rname">Administration</div>
        <NavLink to="/admin" end>Monitoring</NavLink>
        <NavLink to="/admin/review">Review queue</NavLink>
        <NavLink to="/admin/unresolved">Unidentified ingredients</NavLink>
        <NavLink to="/admin/sources">Data sources</NavLink>
        <NavLink to="/admin/audit">Audit log</NavLink>
      </nav>
      <main><Outlet /></main>
    </div>
  );
}

const pct = (v: number | null | undefined) => (v == null ? "–" : `${Math.round(v * 1000) / 10}%`);
const KIND: Record<string, string> = { packaged: "Packaged food", restaurant: "Restaurant", text: "Typed list" };

export function MonitoringPage() {
  const [hours, setHours] = useState(24);
  const [m, setM] = useState<Metrics | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    setM(null);
    api<Metrics>(`/admin/metrics?hours=${hours}`).then(setM).catch(setError);
  }, [hours]);
  if (error) return <ErrorAlert error={error} />;
  const a = m?.assessments;
  return (
    <div className="stack">
      <div className="page-header" style={{ marginBottom: 0 }}>
        <div>
          <h1>Monitoring</h1>
          <p className="muted" style={{ margin: 0 }}>Aggregated from stored checks. No customer identities or allergy profiles are shown here.</p>
        </div>
        <select aria-label="Time window" value={hours} onChange={(e) => setHours(Number(e.target.value))} style={{ width: "auto" }}>
          <option value={24}>Last 24 hours</option>
          <option value={168}>Last 7 days</option>
          <option value={720}>Last 30 days</option>
        </select>
      </div>
      {!m || !a ? <Spinner /> : (
        <>
          <div className="grid-4">
            <div className="stat"><div className="n">{a.total}</div><div className="muted">Checks</div></div>
            <div className={`stat ${a.with_failed_stage ? "attention" : ""}`}><div className="n">{pct(a.failure_rate)}</div><div className="muted">With a failed stage</div></div>
            <div className="stat"><div className="n">{pct(a.with_conflicts_share)}</div><div className="muted">With conflicting sources</div></div>
            <div className="stat"><div className="n">{pct(a.low_confidence_share)}</div><div className="muted">Low-confidence identification</div></div>
            <div className="stat"><div className="n">{pct(a.with_unresolved_share)}</div><div className="muted">With unidentified ingredients</div></div>
            <div className="stat"><div className="n">{m.unresolved_ingredients_open}</div><div className="muted">Unidentified ingredients to review</div></div>
            <div className="stat"><div className="n">{Object.values(m.review_queue_open).reduce((x, y) => x + y, 0)}</div><div className="muted">Open review items</div></div>
            <div className="stat"><div className="n">v{m.knowledge_version}</div><div className="muted">Knowledge base</div></div>
          </div>

          <div className="grid-2">
            <section className="card">
              <h2>Overall outcomes</h2>
              <table><tbody>
                {Object.entries(a.overall_outcomes).map(([o, n]) => (
                  <tr key={o}><td><span className={`badge o-${o}`}>{OUTCOME_LABELS[o as Outcome] ?? o}</span></td><td>{n}</td><td>{pct(a.total ? n / a.total : null)}</td></tr>
                ))}
              </tbody></table>
              <h3 style={{ marginTop: "1rem" }}>By pathway</h3>
              <table><tbody>{Object.entries(a.by_kind).map(([k, n]) => <tr key={k}><td>{KIND[k] ?? k}</td><td>{n}</td></tr>)}</tbody></table>
            </section>
            <section className="card">
              <h2>Response time</h2>
              <table>
                <thead><tr><th>Pathway</th><th>Median</th><th>95th pct</th><th>Max</th></tr></thead>
                <tbody>{Object.entries(a.latency_ms).map(([k, v]) => <tr key={k}><td>{KIND[k] ?? k}</td><td>{v.p50} ms</td><td>{v.p95} ms</td><td>{v.max} ms</td></tr>)}</tbody>
              </table>
              <details style={{ marginTop: "0.8rem" }}>
                <summary>By stage</summary>
                <table>
                  <thead><tr><th>Stage</th><th>Median</th><th>95th pct</th></tr></thead>
                  <tbody>{Object.entries(a.stage_latency_ms).map(([k, v]) => <tr key={k}><td>{k.replace(/_/g, " ")}</td><td>{v.p50} ms</td><td>{v.p95} ms</td></tr>)}</tbody>
                </table>
              </details>
            </section>
          </div>

          <section className="card">
            <h2>Decision rules applied</h2>
            <p className="muted">How often each rule decided a customer's allergen outcome.</p>
            <div className="table-wrap"><table>
              <thead><tr><th>Rule</th><th>Meaning</th><th>Times</th></tr></thead>
              <tbody>{Object.entries(a.decision_rules).sort((x, y) => y[1] - x[1]).map(([r, n]) => <tr key={r}><td><code>{r}</code></td><td>{RULE_LABELS[r] ?? ""}</td><td>{n}</td></tr>)}</tbody>
            </table></div>
            {Object.keys(a.failed_stages).length > 0 && (
              <p style={{ marginTop: "0.6rem" }}><strong>Failed stages:</strong> {Object.entries(a.failed_stages).map(([k, n]) => `${k.replace(/_/g, " ")} (${n})`).join(", ")}</p>
            )}
          </section>

          <section className="card">
            <h2>System</h2>
            <div className="kv">
              {Object.entries(m.caches).map(([k, c]) => (
                <Fragment key={k}><span>Cache: {k.replace(/_/g, " ")}</span><span>{c.hits} hits / {c.misses} misses · hit rate {pct(c.hit_rate ?? null)}</span></Fragment>
              ))}
              <span>Background jobs</span><span>{m.background_jobs.completed} done, {m.background_jobs.failed} failed, {m.background_jobs.pending} pending ({String(m.background_jobs.mode).replace("_", " ")})</span>
              <span>Photo jobs</span><span>{m.image_jobs.running} running of {m.image_jobs.max_concurrent} · {m.image_jobs.rejected_busy} turned away (busy)</span>
            </div>
          </section>
        </>
      )}
    </div>
  );
}

export function AdminReviewPage() {
  return (
    <div>
      <h1>Review queue</h1>
      <ReviewQueue base="/review-queue" canDecide intro="Uncertain input kept for a person to look at: ambiguous or low-confidence product and dish matches, unreadable or possibly incomplete labels, and dishes not on a restaurant's menu. Customers were given a cautious answer meanwhile." />
    </div>
  );
}

export function UnresolvedPage() {
  const [items, setItems] = useState<UnresolvedIngredient[] | null>(null);
  const [concept, setConcept] = useState<Record<number, string>>({});
  const [error, setError] = useState<unknown>(null);
  const load = () => {
    api<UnresolvedIngredient[]>("/knowledge/unresolved?limit=200").then(setItems).catch(setError);
  };
  useEffect(load, []);
  const decide = async (id: number, status: "resolved" | "ignored") => {
    setError(null);
    try {
      await api(`/knowledge/unresolved/${id}`, { method: "POST", json: { status, concept: concept[id] || null } });
      load();
    } catch (err) {
      setError(err);
    }
  };
  return (
    <div>
      <h1>Unidentified ingredients</h1>
      <p className="muted">Ingredient text the knowledge base didn't recognise. Until it's added, checks containing it can't rule allergens out. Add the ingredient through a knowledge proposal (reviewed by a second administrator), then link it here.</p>
      <ErrorAlert error={error} />
      {!items && !error && <Spinner />}
      {items?.length === 0 && <p className="muted">Nothing to review.</p>}
      {items && items.length > 0 && (
        <div className="table-wrap card"><table>
          <thead><tr><th>Text</th><th>Seen</th><th>Where</th><th>Action</th></tr></thead>
          <tbody>
            {items.map((u) => (
              <tr key={u.id}>
                <td><strong>{u.text}</strong><br /><small className="muted">last {formatDate(u.last_seen)}</small></td>
                <td>{u.occurrences}×</td>
                <td><small>{u.source_types.join(", ").replace(/_/g, " ")}</small></td>
                <td>
                  <div className="row" style={{ gap: "0.3rem" }}>
                    <input aria-label={`Concept for ${u.text}`} placeholder="concept slug" value={concept[u.id] ?? ""} onChange={(e) => setConcept({ ...concept, [u.id]: e.target.value })} style={{ width: 140 }} />
                    <button className="small primary" disabled={!concept[u.id]} onClick={() => decide(u.id, "resolved")}>Link</button>
                    <button className="small" onClick={() => decide(u.id, "ignored")}>Not an ingredient</button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table></div>
      )}
    </div>
  );
}

export function SourcesPage() {
  const [rows, setRows] = useState<SourceAdapterInfo[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    api<SourceAdapterInfo[]>("/sources").then(setRows).catch(setError);
  }, []);
  return (
    <div>
      <h1>Data sources</h1>
      <p className="muted">Each source is read through its own adapter. Counts are since the server started.</p>
      <ErrorAlert error={error} />
      {!rows && !error && <Spinner />}
      {rows && (
        <div className="table-wrap card"><table>
          <thead><tr><th>Source</th><th>Calls</th><th>Failures</th><th>Avg time</th><th>Last status</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.name}>
                <td><strong>{r.name.replace(/_/g, " ")}</strong><br /><small className="muted">{r.description}</small></td>
                <td>{r.health.calls}</td>
                <td>{r.health.failures}{r.health.last_failure && <><br /><small className="muted">{r.health.last_failure}</small></>}</td>
                <td>{r.health.average_latency_ms != null ? `${r.health.average_latency_ms} ms` : "–"}</td>
                <td>{r.health.last_status ? <span className={`badge ${["unavailable", "rate_limited", "malformed"].includes(r.health.last_status) ? "o-PRESENT" : "neutral"}`}>{r.health.last_status.replace(/_/g, " ")}</span> : "–"}</td>
              </tr>
            ))}
          </tbody>
        </table></div>
      )}
    </div>
  );
}

export function AdminAuditPage() {
  const [rows, setRows] = useState<AuditEntry[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    api<AuditEntry[]>("/admin/audit?limit=200").then(setRows).catch(setError);
  }, []);
  return (
    <div>
      <h1>Audit log</h1>
      <p className="muted">Administrator actions: knowledge proposals and decisions, review decisions and ingredient reviews.</p>
      <ErrorAlert error={error} />
      {!rows && !error && <Spinner />}
      {rows?.length === 0 && <p className="muted">No administrator actions yet.</p>}
      {rows && rows.length > 0 && (
        <div className="table-wrap card"><table>
          <thead><tr><th>When</th><th>Action</th><th>Item</th><th>Details</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td><small>{formatDate(r.created_at)}</small></td>
                <td>{r.action}</td>
                <td><small>{r.entity_type.replace(/_/g, " ")} #{r.entity_id}</small></td>
                <td><small className="muted">{Object.entries(r.details).filter(([, v]) => v !== null && v !== "").map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : v}`).join(" · ")}</small></td>
              </tr>
            ))}
          </tbody>
        </table></div>
      )}
    </div>
  );
}
