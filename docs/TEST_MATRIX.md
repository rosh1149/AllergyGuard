# Requirements to tests

Each requirement of the design and of the improvement plan, and the automated checks that cover it.
`backend/tests/` holds the unit and API tests (run: `cd backend && pytest`); the evaluation benchmark
and release gate (`python -m app.evaluation`) run in CI on every push; the frontend scripts in
`frontend/e2e/` drive the real application in a browser.

Legend: **T** = pytest file::test, **B** = benchmark dataset (release gate), **E** = browser script.

## 1. Safety rules (the system never certifies food as safe)

| Requirement | Covered by |
|---|---|
| Four outcomes only; no "safe" wording anywhere | T test_engine::test_template_never_claims_safety, ::test_llm_output_checks_reject_safety_claims, ::test_llm_output_checks_require_every_allergen |
| No evidence → Unknown, never "not listed" | T test_engine::test_no_sources_is_unknown_never_not_listed; B safety, uncertainty |
| Presence always wins over cross-contact and absence | T test_engine::test_presence_beats_cross_contact, ::test_disagreement_between_sources_keeps_present; test_stage2::test_allergen_disagreement_keeps_cautious_finding |
| Absence only from a fresh, complete, verified, identity-checked source | T test_engine (low OCR, incomplete, uncertain identity, unverified record), test_evidence::test_stale_record_cannot_rule_out, ::test_old_product_record_cannot_rule_out, test_stage1::test_incomplete_lists_block_absence |
| Candidate sources (public recipes, customer reports, web pages) never decide | T test_engine::test_public_recipe_is_candidate_only, ::test_customer_suggestion_is_candidate_only; test_stage3::test_web_page_never_supports_absence, ::test_public_recipes_never_decide_an_outcome |
| A failed source makes the result Unknown, not "not listed" | T test_packaged::test_database_unreachable; test_step_d::test_rate_limit_is_reported_in_analysis; test_stage5::test_open_circuit_makes_the_product_check_unknown_not_safe |
| Same evidence → same result; the profile selects findings but never changes them | T test_stage2::test_same_evidence_gives_same_result, ::test_profile_selects_findings_but_never_changes_them |
| Release gate: zero missed allergens on the development benchmark | B all nine datasets; T test_evaluation::test_benchmark_passes_release_gate, ::test_gate_blocks_lost_allergen_links |

## 2. Knowledge layer and normalization (plan stage 1)

| Requirement | Covered by |
|---|---|
| Concept types, relation direction, no cycles, sources stated | T test_stage1::test_every_concept_has_one_type_and_seed_passes_rules, ::test_rules_catch_mistakes, ::test_cycles_are_caught |
| Proteins, regulatory categories separate from science | T test_stage1::test_protein_links, ::test_regulatory_category_is_separate_from_science |
| Derived ingredients, wheat/gluten, barley, cocoa vs milk | T test_knowledge (wheat_implies_gluten, barley…, cocoa_does_not_imply_milk, …); B ontology, reasoning |
| Look-alikes (eggplant, coconut, nutmeg …) are not allergens | T test_knowledge::test_lookalike_exclusions_hold_through_concepts; B reasoning ("egg plant") |
| Homonyms keep every meaning | T test_stage1::test_homonym_keeps_both_meanings |
| Cleaning, OCR correction (photos only), spelling, synonyms, fuzzy match that may only add allergens | T test_normalization (15 tests); B normalization, ocr_correction, synonyms |
| Compound ingredients and incomplete component lists | T test_normalization::test_compound_keeps_components_and_skips_class_names, test_stage1::test_parse_marks_incomplete_items; B compound |
| Unresolved text kept, with reasons and candidates, and queued for review | T test_normalization::test_unresolved_text_is_preserved, test_stage1::test_unresolved_reasons_and_candidates, test_knowledge::test_unresolved_queue |
| Knowledge changes: four-eyes approval, versioning, regression and safety gates | T test_knowledge::test_proposal_workflow_four_eyes_and_versioning, ::test_change_that_breaks_regression_cases_is_not_published, test_evaluation::test_knowledge_change_blocked_by_safety_benchmark, test_stage1::test_foodon_mapping_change_is_reviewed_and_logged |

## 3. Reasoning engine (plan stage 2)

| Requirement | Covered by |
|---|---|
| One source-policy table drives fusion; published decision matrix | T test_stage2::test_engine_sets_derive_from_policy_table, ::test_decision_matrix_endpoint |
| Conflicts: kind, handling, status; written differently ≠ conflict | T test_stage2 (3 conflict tests), test_evidence::test_conflict_ids_and_resolution |
| Provenance: path, FoodOn id, allergen category | T test_stage2::test_finding_carries_full_provenance, test_evidence::test_evidence_carries_provenance |
| Cross-contact per allergen and per ingredient | T test_stage2::test_cross_contact_status_per_allergen, ::test_ingredient_level_cross_contact_in_dashboard, test_evidence (cross-contact tests) |
| Decision rules R1–R6 recorded in the trace | T test_step_e::test_decision_rules, ::test_trace_is_recorded_without_personal_data |

## 4. Input pathways (plan stage 3)

| Requirement | Covered by |
|---|---|
| Label photo, barcode, typed barcode, name search, image quality | T test_packaged (15 tests); E walkthrough.cjs |
| Deskew and tolerant OCR heading | T test_stage3::test_deskew_estimates_tilt, ::test_tolerant_heading_and_metrics |
| Manufacturer pages only from approved domains; web pages as candidates | T test_stage3 (3 tests) |
| Restaurant: verified records, dish matching, branches, unregistered restaurants | T test_restaurant_pipeline (12 tests) |
| Recipe versions, scheduled changes, another branch's dish never used | T test_stage3::test_scheduled_version_applies_only_from_its_date, ::test_current_version_selection_by_time, ::test_dish_from_another_branch_is_never_used; test_dashboard::test_menu_and_recipe_versions |
| Feedback never changes trusted records until confirmed; clarification | T test_feedback (4 tests), test_step_d::test_clarification_round_trip, ::test_incorrect_allergen_is_never_applied_automatically |

## 5. Research evaluation (plan stage 4)

| Requirement | Covered by |
|---|---|
| Frozen held-out set, unchanged and run only with `--final` | T test_heldout_frozen (3 tests) |
| Baselines B1–B3 and ablation switches | T test_stage4 (5 tests) |
| Benchmark gate blocks regressions and shrinking datasets | T test_evaluation (7 tests) |

## 6. Engineering maturity (plan stage 5)

| Requirement | Covered by |
|---|---|
| Retries with backoff for unavailable sources; no retry on rate limits or not-found | T test_stage5::test_temporary_outage_is_retried, ::test_retries_are_bounded, ::test_rate_limits_and_not_found_are_not_retried, ::test_backoff_grows |
| Circuit breaker opens, short-circuits, half-opens, closes | T test_stage5::test_circuit_opens_after_repeated_failures_and_skips_calls, ::test_half_open_trial_closes_or_reopens, ::test_not_found_does_not_count_as_a_failure |
| An adapter bug becomes a failed source, not a crash | T test_step_d::test_adapter_bug_becomes_failed_source |
| Photo-job limit, timings, CPU and memory monitoring | T test_step_e::test_image_jobs_are_limited, test_stage5::test_image_job_limit_defaults_to_cores, ::test_image_job_timings_are_recorded, ::test_metrics_report_process_resources, ::test_tesseract_runs_single_threaded; performance in docs/PERFORMANCE.md |
| Caches | T test_step_e::test_normalization_cache, ::test_off_cache, ::test_saved_copy_when_database_fails |
| Admin metrics and assessment trace lookup without personal data | T test_step_e::test_metrics_endpoint, test_stage5::test_admin_trace_lookup_shows_no_personal_data, ::test_trace_lookup_is_admin_only |
| Migrations: parity with models, rollback, atomic failure, adoption of older databases | T test_migrations (5 tests) |
| Tests refuse a non-test database | T test_migrations::test_the_test_suite_refuses_a_non_test_database |

## 7. Security and privacy

| Requirement | Covered by |
|---|---|
| Allergies captured at sign-up and used automatically | T test_foundation::test_customer_signup_stores_allergies, ::test_signup_requires_at_least_one_allergy, test_analyze_api::test_analyze_text_uses_saved_profile |
| Tenant isolation on every dashboard route | T test_step_e::test_tenant_isolation_sweep, test_dashboard::test_tenant_isolation, test_foundation::test_restaurant_signup_and_tenant_isolation |
| Restaurant roles (staff / manager / owner) on every dashboard route | T test_security::test_every_dashboard_route_declares_its_roles, ::test_role_sweep_staff_and_managers, ::test_staff_cannot_change_roles_or_promote_themselves, test_dashboard::test_team_roles_and_permissions |
| Customer-only and admin-only routes | T test_security::test_customer_and_admin_routes_refuse_other_account_types, test_step_e::test_role_boundaries, test_knowledge::test_admin_only |
| Tokens: expiry, forged, unsigned, malformed, deleted accounts | T test_security::test_tokens_expired_forged_or_malformed_are_refused, ::test_token_lifetime_is_bounded, test_foundation::test_unauthenticated_and_bad_token |
| Login throttling | T test_step_e::test_login_throttling |
| Upload abuse: non-images, empty, too many, oversized, decompression bombs | T test_security (4 upload tests) |
| Nothing private without signing in; public menus without recipes or identities | T test_security::test_only_auth_and_health_work_without_signing_in, ::test_public_directory_and_menus_expose_no_private_data, ::test_staff_view_of_feedback_hides_the_customer |
| Logs without personal data | T test_security::test_logs_contain_no_personal_data |
| History private; export and deletion | T test_evidence::test_assessment_history_is_private, test_step_e::test_export_and_delete_account |
| Security headers and request ids | T test_step_e::test_request_id_and_security_headers |
| Administrator actions audited | T test_step_e::test_admin_actions_are_audited |

## 8. User interface

| Requirement | Covered by |
|---|---|
| Customer checks, results with "Why", history, profile, feedback | E walkthrough.cjs, step_g.cjs |
| Restaurant dashboard: menu, recipe editor, versions, feedback, review | E step_g.cjs |
| Admin: monitoring (incl. CPU/memory), review queues, sources, manufacturer sites, check traces, audit | E step_g.cjs; check traces page verified in a browser for Stage 5 |

## Not covered by automated tests

- OCR accuracy on **real** label photos (only generated images are tested; needs a collected photo set).
- Correctness of the allergen knowledge against the FSSAI regulation text (needs the official text and
  expert review of `docs/expert-review-sheet.csv`).
- Live behaviour of external services (Open Food Facts, Brave search): tests use simulated responses.
