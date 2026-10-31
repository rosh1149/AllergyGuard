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
  matched_as?: string;
  match_method?: string;
  match_confidence?: number | null;
  concept?: string;
  relationship_path?: string;
  certainty?: string;
  source_reference?: string;
  source_verification?: string;
  source_as_of?: string | null;
  conflict_ids?: string[];
  path_nodes?: string[];
  foodon_id?: string | null;
  allergen_category?: string;
}

export interface AllergenAssessment {
  code: string;
  name: string;
  is_custom: boolean;
  outcome: Outcome;
  outcome_label: string;
  reasons: string[];
  evidence: EvidenceRef[];
  basis?: string[];
  rules?: string[];
  cross_contact?: "reported" | "none_reported" | "not_assessed";
  regulated_in?: string[];
}

export interface Conflict {
  id?: string;
  resolution?: string;
  handling?: string;
  status?: "resolved" | "unresolved";
  only_in?: Record<string, string[]>;
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
  reference?: string;
  verification?: string;
  as_of?: string | null;
  age_days?: number | null;
  fresh?: boolean;
  completeness?: "complete" | "partial" | "unavailable" | "conflicting";
  unresolved_items?: string[];
}

export interface StageRecord {
  name: string;
  status: string;
  duration_ms: number | null;
  counts: Record<string, number | string>;
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
  knowledge_version?: number;
  assessment_id?: number | null;
  evaluated_at?: string | null;
  trace?: StageRecord[];
  duration_ms?: number | null;
}

export interface Step {
  name: string;
  status: "done" | "skipped" | "failed";
  detail: string;
  duration_ms?: number | null;
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
  product_candidates?: { barcode: string; name: string; similarity: number }[];
  needs_confirmation?: boolean;
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
  needs_confirmation?: boolean;
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
  | "incorrect_allergen"
  | "cross_contact"
  | "outdated_recipe"
  | "other";
export type SuggestionStatus = "unverified" | "clarification_requested" | "confirmed" | "rejected";

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
  clarification_request?: string;
  clarification_response?: string;
  clarified_at?: string | null;
  impact?: {
    resulting_version_number: number;
    version_is_current: boolean;
    assessments_since: number;
    assessments_flagging_allergen: number | null;
  } | null;
  history?: { action: string; at: string; note: string }[];
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
export type CrossContactKind =
  | "shared_equipment" | "shared_fryer" | "preparation_area" | "utensils" | "storage"
  | "manufacturing_facility" | "supplier" | "other";
export type CrossContactStatus = "verified" | "reported" | "unknown";

export interface CrossContactRecord {
  allergen_code: string;
  level: CrossContactLevel;
  description: string;
  kind: CrossContactKind;
  status: CrossContactStatus;
  ingredient?: string;
}

export interface VersionChanges {
  previous_version: number | null;
  ingredients_added: string[];
  ingredients_removed: string[];
  allergens_added: string[];
  allergens_removed: string[];
  cross_contact_added: string[];
  cross_contact_removed: string[];
}

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
  cross_contacts: CrossContactRecord[];
  cross_contact_reviewed?: boolean;
  changes?: VersionChanges | null;
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
  cross_contacts: CrossContactRecord[];
  cross_contact_reviewed: boolean;
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

// ---- history, review queue, monitoring ----

export interface AssessmentSummary {
  id: number;
  kind: "packaged" | "restaurant" | "text";
  label: string;
  overall_outcome: Outcome;
  outcomes: Record<string, Outcome>;
  knowledge_version: number;
  created_at: string;
  outdated: boolean;
  outdated_reasons: string[];
}

export interface AssessmentDetail extends AssessmentSummary {
  sources: Record<string, unknown>[];
  result: AnalysisResult;
}

export type ReviewKind =
  | "low_confidence_product" | "multiple_product_matches" | "unreadable_label" | "incomplete_label"
  | "low_confidence_dish" | "multiple_dish_matches" | "unmatched_dish";

export interface ReviewItem {
  id: number;
  kind: ReviewKind;
  status: "open" | "resolved" | "dismissed";
  restaurant_id: number | null;
  key: string;
  summary: string;
  details: Record<string, unknown>;
  occurrences: number;
  first_seen: string;
  last_seen: string;
  resolution_note: string;
  resolved_at: string | null;
}

export interface UnresolvedIngredient {
  id: number;
  text: string;
  occurrences: number;
  source_types: string[];
  candidates?: string[];
  reasons?: string[];
  first_seen: string;
  last_seen: string;
  status: string;
}

export interface SourceAdapterInfo {
  name: string;
  source_type: string;
  description: string;
  health: { calls: number; failures: number; last_status?: string; last_failure?: string; average_latency_ms?: number };
}

export interface Percentiles { count?: number; p50: number | null; p95: number | null; max?: number }

export interface Metrics {
  window_hours: number;
  assessments: {
    total: number;
    by_kind: Record<string, number>;
    overall_outcomes: Record<string, number>;
    latency_ms: Record<string, Percentiles>;
    stage_latency_ms: Record<string, Percentiles>;
    with_conflicts: number;
    with_conflicts_share: number | null;
    low_confidence_identification: number;
    low_confidence_share: number | null;
    with_unresolved_ingredients: number;
    with_unresolved_share: number | null;
    with_failed_stage: number;
    failure_rate: number | null;
    failed_stages: Record<string, number>;
    decision_rules: Record<string, number>;
  };
  review_queue_open: Record<string, number>;
  unresolved_ingredients_open: number;
  knowledge_version: number;
  caches: Record<string, { hits: number; misses: number; hit_rate?: number | null; items?: number }>;
  background_jobs: Record<string, number | string>;
  image_jobs: Record<string, number>;
}
