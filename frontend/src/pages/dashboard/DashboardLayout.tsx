import { useCallback, useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { api } from "../../api/client";
import type { Dashboard } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { ErrorAlert, Spinner } from "../../components/ui";
import { DashboardContext } from "./context";

export function DashboardLayout() {
  const { user } = useAuth();
  const rid = user?.memberships[0]?.restaurant_id;
  const [dash, setDash] = useState<Dashboard | null>(null);
  const [error, setError] = useState<unknown>(null);

  const reload = useCallback(() => {
    if (rid) api<Dashboard>(`/restaurants/${rid}`).then(setDash).catch(setError);
  }, [rid]);
  useEffect(reload, [reload]);

  if (!rid) {
    return <div className="page"><div className="alert info">Your account isn't a member of any restaurant yet. Ask the restaurant owner to add you to their team.</div></div>;
  }
  if (error) return <div className="page"><ErrorAlert error={error} /></div>;
  if (!dash) return <div className="page"><Spinner /></div>;

  const canEdit = dash.your_role !== "staff";
  const isOwner = dash.your_role === "owner";
  return (
    <DashboardContext.Provider value={{ rid, dash, reload, canEdit, isOwner }}>
      <div className="dash">
        <nav className="sidebar" aria-label="Dashboard">
          <div className="rname">{dash.restaurant.name}</div>
          <NavLink to="/dashboard" end>Overview</NavLink>
          <NavLink to="/dashboard/menu">Menu</NavLink>
          <NavLink to="/dashboard/feedback">
            Feedback {dash.pending_feedback > 0 && <span className="badge o-POSSIBLE_CROSS_CONTACT">{dash.pending_feedback}</span>}
          </NavLink>
          <NavLink to="/dashboard/review">Uncertain matches</NavLink>
          <NavLink to="/dashboard/branches">Branches</NavLink>
          <NavLink to="/dashboard/team">Team</NavLink>
          <NavLink to="/dashboard/settings">Settings</NavLink>
          {canEdit && <NavLink to="/dashboard/audit">Audit log</NavLink>}
        </nav>
        <main>
          <Outlet />
        </main>
      </div>
    </DashboardContext.Provider>
  );
}
