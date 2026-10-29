import { NavLink, Navigate, Outlet, Route, Routes } from "react-router-dom";
import type { UserRole } from "./api/types";
import { useAuth } from "./auth/AuthContext";
import { Spinner } from "./components/ui";
import { LoginPage, RestaurantSignupPage, SignupPage } from "./pages/Auth";
import { HomePage } from "./pages/customer/Home";
import { PackagedCheckPage } from "./pages/customer/PackagedCheck";
import { MyFeedbackPage, ProfilePage } from "./pages/customer/Profile";
import { RestaurantCheckPage } from "./pages/customer/RestaurantCheck";
import { TextCheckPage } from "./pages/customer/TextCheck";
import { AuditPage, BranchesPage, SettingsPage, TeamPage } from "./pages/dashboard/Admin";
import { DashboardLayout } from "./pages/dashboard/DashboardLayout";
import { FeedbackPage } from "./pages/dashboard/Feedback";
import { MenuItemPage, MenuPage, NewMenuItemPage } from "./pages/dashboard/Menu";
import { OverviewPage } from "./pages/dashboard/Overview";

function TopBar() {
  const { user, logout } = useAuth();
  const isCustomer = user?.role === "customer";
  return (
    <header className="topbar">
      <div className="topbar-inner">
        <NavLink to={isCustomer ? "/" : "/dashboard"} className="brand">
          <img src="/favicon.svg" alt="" /> AllergyGuard
        </NavLink>
        <nav className="nav" aria-label="Main">
          {isCustomer ? (
            <>
              <NavLink to="/" end>Home</NavLink>
              <NavLink to="/check/packaged">Packaged food</NavLink>
              <NavLink to="/check/restaurant">Restaurant food</NavLink>
              <NavLink to="/feedback">My suggestions</NavLink>
              <NavLink to="/profile">My allergies</NavLink>
            </>
          ) : (
            <NavLink to="/dashboard">Restaurant dashboard</NavLink>
          )}
        </nav>
        <div className="user">
          <span>{user?.full_name}</span>
          <button className="small" onClick={logout}>Sign out</button>
        </div>
      </div>
    </header>
  );
}

function RequireRole({ role }: { role: UserRole }) {
  const { user, loading } = useAuth();
  if (loading) return <div className="page"><Spinner /></div>;
  if (!user) return <Navigate to="/login" replace />;
  if (user.role !== role) return <Navigate to={user.role === "customer" ? "/" : "/dashboard"} replace />;
  return (
    <>
      <TopBar />
      <Outlet />
    </>
  );
}

function GuestOnly() {
  const { user, loading } = useAuth();
  if (loading) return <div className="page"><Spinner /></div>;
  if (user) return <Navigate to={user.role === "customer" ? "/" : "/dashboard"} replace />;
  return <Outlet />;
}

export function App() {
  return (
    <Routes>
      <Route element={<GuestOnly />}>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/signup" element={<SignupPage />} />
        <Route path="/signup/restaurant" element={<RestaurantSignupPage />} />
      </Route>
      <Route element={<RequireRole role="customer" />}>
        <Route path="/" element={<HomePage />} />
        <Route path="/check/packaged" element={<PackagedCheckPage />} />
        <Route path="/check/restaurant" element={<RestaurantCheckPage />} />
        <Route path="/check/text" element={<TextCheckPage />} />
        <Route path="/profile" element={<ProfilePage />} />
        <Route path="/feedback" element={<MyFeedbackPage />} />
      </Route>
      <Route element={<RequireRole role="restaurant" />}>
        <Route path="/dashboard" element={<DashboardLayout />}>
          <Route index element={<OverviewPage />} />
          <Route path="menu" element={<MenuPage />} />
          <Route path="menu/new" element={<NewMenuItemPage />} />
          <Route path="menu/:itemId" element={<MenuItemPage />} />
          <Route path="feedback" element={<FeedbackPage />} />
          <Route path="branches" element={<BranchesPage />} />
          <Route path="team" element={<TeamPage />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="audit" element={<AuditPage />} />
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
