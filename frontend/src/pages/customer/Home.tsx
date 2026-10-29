import { Link } from "react-router-dom";
import { useAuth } from "../../auth/AuthContext";

export function HomePage() {
  const { user } = useAuth();
  const profile = user?.allergy_profile;
  const names = [...(profile?.allergens.map((a) => a.name) ?? []), ...(profile?.custom_allergies ?? [])];
  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Hi {user?.full_name.split(" ")[0]}</h1>
          <p className="muted" style={{ margin: 0 }}>What would you like to check?</p>
        </div>
      </div>
      <div className="card" style={{ marginBottom: "1.25rem" }}>
        <div className="spread">
          <div>
            <strong>Checking for: </strong>
            <span className="chips" style={{ display: "inline-flex" }}>
              {names.map((n) => (
                <span key={n} className="chip">{n}</span>
              ))}
            </span>
          </div>
          <Link to="/profile">Edit allergies</Link>
        </div>
      </div>
      <div className="grid-2">
        <Link to="/check/packaged" className="choice">
          <div className="icon" aria-hidden>📦</div>
          <h2>Packaged food</h2>
          <p className="muted">Photograph the product and its ingredient label, or enter the barcode.</p>
        </Link>
        <Link to="/check/restaurant" className="choice">
          <div className="icon" aria-hidden>🍽️</div>
          <h2>Restaurant food</h2>
          <p className="muted">Choose the restaurant and branch, then photograph or name your dish.</p>
        </Link>
      </div>
      <p style={{ marginTop: "1.25rem" }}>
        Have the ingredient list as text? <Link to="/check/text">Type or paste it instead</Link>.
      </p>
    </div>
  );
}
