import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { TokenResponse } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { AllergenPicker, type AllergySelection } from "../components/AllergenPicker";
import { ErrorAlert, Field } from "../components/ui";

function Brand() {
  return (
    <div className="auth-brand">
      <img src="/favicon.svg" alt="" /> AllergyGuard
    </div>
  );
}

export function LoginPage() {
  const { login } = useAuth();
  const nav = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const user = await login(email, password);
      nav(user.role === "restaurant" ? "/dashboard" : "/");
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-wrap">
      <div className="auth-card">
        <Brand />
        <form className="card" onSubmit={submit}>
          <h1>Sign in</h1>
          <ErrorAlert error={error} />
          <Field label="Email">
            <input type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
          </Field>
          <Field label="Password">
            <input type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
          </Field>
          <button className="primary block" disabled={busy}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
          <p style={{ marginTop: "1rem", textAlign: "center" }}>
            New here? <Link to="/signup">Create an account</Link> · <Link to="/signup/restaurant">Register a restaurant</Link>
          </p>
          {import.meta.env.DEV && (
            <p className="muted" style={{ fontSize: "0.85rem", textAlign: "center" }}>
              Demo: customer@demo.allergyguard.app / owner@demo.allergyguard.app — password Demo1234!
            </p>
          )}
        </form>
      </div>
    </div>
  );
}

export function SignupPage() {
  const { acceptToken } = useAuth();
  const nav = useNavigate();
  const [form, setForm] = useState({ full_name: "", email: "", password: "" });
  const [allergies, setAllergies] = useState<AllergySelection>({ allergen_codes: [], custom_allergies: [] });
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const none = allergies.allergen_codes.length === 0 && allergies.custom_allergies.length === 0;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (none) {
      setError(new Error("Choose at least one allergy. AllergyGuard checks food against these."));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      acceptToken(
        await api<TokenResponse>("/auth/register/customer", {
          method: "POST",
          json: { ...form, allergy_profile: allergies },
        }),
      );
      nav("/");
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-wrap">
      <div className="auth-card wide">
        <Brand />
        <form className="card" onSubmit={submit}>
          <h1>Create your account</h1>
          <ErrorAlert error={error} />
          <div className="grid-2">
            <Field label="Your name">
              <input required autoComplete="name" value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} />
            </Field>
            <Field label="Email">
              <input type="email" required autoComplete="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
            </Field>
          </div>
          <Field label="Password" hint="At least 8 characters.">
            <input type="password" required minLength={8} autoComplete="new-password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
          </Field>
          <h2 style={{ marginTop: "0.5rem" }}>Your allergies</h2>
          <p className="muted">Every check uses these automatically. You can change them later in your profile.</p>
          <AllergenPicker value={allergies} onChange={setAllergies} />
          <button className="primary block" style={{ marginTop: "1.2rem" }} disabled={busy}>
            {busy ? "Creating account…" : "Create account"}
          </button>
          <p style={{ marginTop: "1rem", textAlign: "center" }}>
            Already have an account? <Link to="/login">Sign in</Link>
          </p>
        </form>
      </div>
    </div>
  );
}

export function RestaurantSignupPage() {
  const { acceptToken } = useAuth();
  const nav = useNavigate();
  const [form, setForm] = useState({
    full_name: "", email: "", password: "", restaurant_name: "", cuisine: "",
    branch_name: "Main branch", branch_address: "", branch_city: "",
  });
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      acceptToken(await api<TokenResponse>("/auth/register/restaurant", { method: "POST", json: form }));
      nav("/dashboard");
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-wrap">
      <div className="auth-card wide">
        <Brand />
        <form className="card" onSubmit={submit}>
          <h1>Register your restaurant</h1>
          <p className="muted">Manage menus, ingredients, allergen and cross-contact records, and review customer feedback.</p>
          <ErrorAlert error={error} />
          <div className="grid-2">
            <Field label="Restaurant name"><input required value={form.restaurant_name} onChange={set("restaurant_name")} /></Field>
            <Field label="Cuisine"><input value={form.cuisine} onChange={set("cuisine")} placeholder="e.g. Indian" /></Field>
            <Field label="First branch name"><input required value={form.branch_name} onChange={set("branch_name")} /></Field>
            <Field label="City"><input value={form.branch_city} onChange={set("branch_city")} /></Field>
          </div>
          <Field label="Branch address"><input value={form.branch_address} onChange={set("branch_address")} /></Field>
          <h2>Owner account</h2>
          <div className="grid-2">
            <Field label="Your name"><input required value={form.full_name} onChange={set("full_name")} /></Field>
            <Field label="Email"><input type="email" required value={form.email} onChange={set("email")} /></Field>
          </div>
          <Field label="Password" hint="At least 8 characters.">
            <input type="password" required minLength={8} value={form.password} onChange={set("password")} />
          </Field>
          <button className="primary block" disabled={busy}>{busy ? "Registering…" : "Register restaurant"}</button>
          <p style={{ marginTop: "1rem", textAlign: "center" }}>
            Already registered? <Link to="/login">Sign in</Link>
          </p>
        </form>
      </div>
    </div>
  );
}
