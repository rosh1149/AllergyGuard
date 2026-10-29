import { Link } from "react-router-dom";
import { useDash } from "./context";

export function OverviewPage() {
  const { dash, canEdit } = useDash();
  return (
    <div className="stack">
      <div className="page-header" style={{ marginBottom: 0 }}>
        <div>
          <h1>{dash.restaurant.name}</h1>
          <p className="muted" style={{ margin: 0 }}>
            {dash.restaurant.cuisine || "Restaurant"} · you are {dash.your_role === "owner" ? "an owner" : `a ${dash.your_role}`}
            {!canEdit && " (read-only)"}
          </p>
        </div>
        {canEdit && <Link to="/dashboard/menu/new"><button className="primary">+ Add dish</button></Link>}
      </div>
      <div className="grid-4">
        <div className="stat"><div className="n">{dash.menu_items}</div><div className="muted">Menu items</div></div>
        <Link to="/dashboard/menu?filter=unverified" className={`stat ${dash.unverified_recipes ? "attention" : ""}`} style={{ textDecoration: "none", color: "inherit" }}>
          <div className="n">{dash.unverified_recipes}</div><div className="muted">Recipes awaiting verification</div>
        </Link>
        <Link to="/dashboard/feedback" className={`stat ${dash.pending_feedback ? "attention" : ""}`} style={{ textDecoration: "none", color: "inherit" }}>
          <div className="n">{dash.pending_feedback}</div><div className="muted">Customer suggestions to review</div>
        </Link>
        <div className="stat"><div className="n">{dash.team_members}</div><div className="muted">Team members</div></div>
      </div>
      <div className="card">
        <h2>Branches</h2>
        <ul className="plain">
          {dash.branches.map((b) => (
            <li key={b.id} className="spread">
              <span><strong>{b.name}</strong> <small className="muted">{[b.address, b.city].filter(Boolean).join(", ")}</small></span>
              <span className={`badge ${b.is_active ? "ok" : "neutral"}`}>{b.is_active ? "Active" : "Inactive"}</span>
            </li>
          ))}
        </ul>
      </div>
      <div className="alert info">
        Only <strong>verified</strong> recipe versions can tell a customer that an allergen is not listed. Unverified recipes can still show allergens as present.
      </div>
    </div>
  );
}
