import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../../api/client";
import type { AssessmentDetail, AssessmentSummary } from "../../api/types";
import { ResultView } from "../../components/ResultView";
import { ErrorAlert, formatDate, OutcomeBadge, Spinner } from "../../components/ui";

const KIND: Record<string, string> = { packaged: "Packaged food", restaurant: "Restaurant dish", text: "Typed ingredient list" };
const RECHECK: Record<string, string> = { packaged: "/check/packaged", restaurant: "/check/restaurant", text: "/check/text" };

function Outdated({ reasons }: { reasons: string[] }) {
  return (
    <div className="alert warn" role="status">
      <strong>This result may be out of date.</strong>
      <ul style={{ margin: "0.3rem 0 0", paddingLeft: "1.1rem" }}>{reasons.map((r) => <li key={r}>{r}</li>)}</ul>
    </div>
  );
}

export function HistoryPage() {
  const [items, setItems] = useState<AssessmentSummary[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    api<AssessmentSummary[]>("/me/assessments?limit=100").then(setItems).catch(setError);
  }, []);
  return (
    <div className="page narrow">
      <h1>Your past checks</h1>
      <p className="muted">Each check is saved with the evidence it used (never your photos). We flag a check when the recipe, the product record or our allergen knowledge has changed since.</p>
      <ErrorAlert error={error} />
      {!items && !error && <Spinner />}
      {items?.length === 0 && <p className="muted">No checks yet.</p>}
      {items && items.length > 0 && (
        <ul className="plain card">
          {items.map((a) => (
            <li key={a.id}>
              <div className="spread">
                <div>
                  <Link to={`/history/${a.id}`}><strong>{a.label || KIND[a.kind]}</strong></Link>
                  <div><small className="muted">{KIND[a.kind]} · {formatDate(a.created_at)}</small></div>
                </div>
                <span className="row" style={{ gap: "0.3rem" }}>
                  {a.outdated && <span className="badge o-POSSIBLE_CROSS_CONTACT" title={a.outdated_reasons.join(" ")}>May be outdated</span>}
                  <OutcomeBadge outcome={a.overall_outcome} />
                </span>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function HistoryDetailPage() {
  const { id } = useParams();
  const nav = useNavigate();
  const [a, setA] = useState<AssessmentDetail | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    api<AssessmentDetail>(`/me/assessments/${id}`).then(setA).catch(setError);
  }, [id]);
  const remove = async () => {
    if (!confirm("Delete this saved check?")) return;
    try {
      await api(`/me/assessments/${id}`, { method: "DELETE" });
      nav("/history");
    } catch (err) {
      setError(err);
    }
  };
  if (error) return <div className="page narrow"><ErrorAlert error={error} /></div>;
  if (!a) return <div className="page narrow"><Spinner /></div>;
  return (
    <div className="page narrow stack">
      <div className="spread">
        <div>
          <Link to="/history">← Past checks</Link>
          <h1 style={{ marginTop: "0.3rem" }}>{a.label || KIND[a.kind]}</h1>
          <p className="muted" style={{ margin: 0 }}>{KIND[a.kind]} · checked {formatDate(a.created_at)} · knowledge base v{a.knowledge_version}</p>
        </div>
        <div className="row">
          <Link to={RECHECK[a.kind]}><button className="small primary">Check again</button></Link>
          <button className="small danger" onClick={remove}>Delete</button>
        </div>
      </div>
      {a.outdated && <Outdated reasons={a.outdated_reasons} />}
      <ResultView result={a.result} saved={false} />
    </div>
  );
}
