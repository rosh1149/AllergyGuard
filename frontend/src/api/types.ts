// Mirrors the backend response models (backend/app/**/schemas, engine/types.py).

export type Outcome = "PRESENT" | "POSSIBLE_CROSS_CONTACT" | "UNKNOWN" | "NOT_LISTED_IN_VERIFIED_RECORDS";
export type UserRole = "customer" | "restaurant" | "admin";
export type MembershipRole = "owner" | "manager" | "staff";

export interface Allergen {
  id: number;
  code: string;
  name: string;
  description: string;
}

export interface AllergyProfile {
  allergens: Allergen[];
  custom_allergies: string[];
}

export interface Membership {
  restaurant_id: number;
  restaurant_name: string;
  role: MembershipRole;
}

export interface User {
  id: number;
  email: string;
  full_name: string;
  role: UserRole;
  allergy_profile: AllergyProfile | null;
  memberships: Membership[];
}

export interface TokenResponse {
  access_token: string;
  user: User;
}

// ---- analysis ----

export interface EvidenceRef {
  source_id: string;
  source_name: string;
  source_type: string;
  kind: string;
  ingredient: string;
  matched_term: string;
  detail: string;
}

export interface AllergenAssessment {
  code: string;
  name: string;
  is_custom: boolean;
  outcome: Outcome;
  outcome_label: string;
  reasons: string[];
  evidence: EvidenceRef[];
}

export interface Conflict {
  kind: string;
  allergen_code: string | null;
  description: string;
  source_ids: string[];
}

export interface SourceSummary {
  id: string;
  type: string;
  type_label: string;
  name: string;
  url: string | null;
  ingredients: string[];
  complete: boolean;
  confidence: number;
  used_for_absence: boolean;
  not_used_for_absence_reason: string;
}

export interface AnalysisResult {
  overall_outcome: Outcome;
  overall_label: string;
  identity_label: string;
  your_allergens: AllergenAssessment[];
  other_allergens: AllergenAssessment[];
  sources: SourceSummary[];
  conflicts: Conflict[];
  limitations: string[];
  nonspecific_ingredients: string[];
  explanation: string;
  explanation_source: "template" | "llm";
  disclaimer: string;
}

export interface Step {
  name: string;
  status: "done" | "skipped" | "failed";
  detail: string;
}

export interface PackagedAnalysis {
  result: AnalysisResult;
  product: {
    barcode: string;
    name: string;
    brand: string;
    quantity: string;
    image_url: string;
    source_url: string;
    identification_method: string;
    identity_confidence: number;
    confident: boolean;
  } | null;
  label: {
    ocr_available: boolean;
    ingredients_found: boolean;
    heading_found: boolean;
    ingredients_text: string;
    confidence: number;
  };
  images: { width: number; height: number; sharpness: number; brightness: number; issues: string[] }[];
  steps: Step[];
}

export interface DishCandidate {
  menu_item_id: number;
  name: string;
  confidence: number;
}

export interface RestaurantAnalysis {
  result: AnalysisResult;
  restaurant: {
    registered: boolean;
    restaurant_id: number | null;
    name: string;
    branch_id: number | null;
    branch_name: string;
    location: string;
  };
  dish: {
    menu_item_id: number;
    name: string;
    match_method: string;
    match_confidence: number;
    confident: boolean;
    recipe_version: number | null;
    verification_status: "verified" | "unverified" | null;
  } | null;
  candidates: DishCandidate[];
  questions_for_staff: string[];
  public_recipe_sources: { title: string; url: string }[];
  steps: Step[];
  can_submit_feedback: boolean;
}

// ---- restaurants ----

export interface PublicBranch {
  id: number;
  name: string;
  city: string;
  address: string;
}

export interface PublicRestaurant {
  id: number;
  name: string;
  cuisine: string;
  branches: PublicBranch[];
}

export interface PublicMenuItem {
  id: number;
  name: string;
  category: string;
  description: string;
}

export type SuggestionType =
  | "missing_ingredient"
  | "incorrect_ingredient"
  | "missing_allergen"
  | "cross_contact"
  | "other";
export type SuggestionStatus = "unverified" | "confirmed" | "rejected";

export interface Feedback {
  id: number;
  restaurant_id: number;
  branch_id: number;
  menu_item_id: number;
  menu_item_name: string;
  recipe_version_number: number | null;
  suggestion_type: SuggestionType;
  ingredient_name: string;
  allergen_code: string | null;
  message: string;
  status: SuggestionStatus;
  created_at: string;
  decided_at: string | null;
  decision_note: string;
  resulting_version_number: number | null;
}

// ---- dashboard ----

export interface Restaurant {
  id: number;
  name: string;
  slug: string;
  cuisine: string;
  description: string;
  is_active: boolean;
  settings: Record<string, unknown>;
}

export interface Branch {
  id: number;
  name: string;
  address: string;
  city: string;
  is_active: boolean;
}

export interface Dashboard {
  restaurant: Restaurant;
  your_role: MembershipRole;
  branches: Branch[];
  menu_items: number;
  unverified_recipes: number;
  pending_feedback: number;
  team_members: number;
}

export interface Member {
  user_id: number;
  email: string;
  full_name: string;
  role: MembershipRole;
}

export type CrossContactLevel = "known" | "possible";

export interface IngredientOut {
  id: number;
  name: string;
  notes: string;
  allergen_codes: string[];
}

export interface RecipeVersion {
  id: number;
  version_number: number;
  is_current: boolean;
  verification_status: "verified" | "unverified";
  change_note: string;
  created_at: string;
  created_by: string | null;
  verified_by: string | null;
  verified_at: string | null;
  ingredients: IngredientOut[];
  dish_allergen_codes: string[];
  cross_contacts: { allergen_code: string; level: CrossContactLevel; description: string }[];
  warnings: { ingredient: string; allergen_code: string; allergen_name: string; message: string }[];
}

export type MenuItemStatus = "available" | "unavailable" | "archived";

export interface MenuItem {
  id: number;
  branch_id: number;
  branch_name: string;
  name: string;
  description: string;
  category: string;
  status: MenuItemStatus;
  current_version: RecipeVersion | null;
  pending_feedback: number;
}

export interface MenuItemDetail extends MenuItem {
  versions: RecipeVersion[];
}

export interface RecipeInput {
  ingredients: { name: string; notes: string; allergen_codes: string[] }[];
  dish_allergen_codes: string[];
  cross_contacts: { allergen_code: string; level: CrossContactLevel; description: string }[];
  change_note: string;
  mark_verified: boolean;
}

export interface AuditEntry {
  id: number;
  actor_id: number | null;
  action: string;
  entity_type: string;
  entity_id: number | null;
  details: Record<string, unknown>;
  created_at: string;
}
