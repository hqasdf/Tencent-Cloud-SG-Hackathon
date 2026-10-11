# Phase 4 baseline failure classification

The isolated baseline contained 829 tests: **726 passed / 103 failed**. The previous reported run was 725 / 103; one subsequently added replay regression accounts for the extra passing test.

First failure cause: A (obsolete fixtures) **79**; B (outdated contracts) **24**; C (genuine baseline regressions) **0**; D (unknown) **0**.

Several A tests also needed atomic-contract updates after their missing fixture was repaired. This secondary cause is recorded below; each original failed test is counted once.

These fixture/contract failures blocked useful verification, but did not establish broken production behavior. New Phase 4 tests separately discovered a required-route-evidence status defect and a mock Judge wait-rule defect. Frontend review found stale-response and duplicate-action risks.

| Category | Original failed test | Root cause and correction |
|---|---|---|
| A | `tests/test_advocate_agents.py::test_agent_produces_validated_output_in_mock_mode` | Removed case fixture; secondary prompt/schema expectation now follows the structured atomic contract. |
| A | `tests/test_advocate_agents.py::test_driver_agent_produces_validated_output_in_mock_mode` | Removed case fixture; secondary prompt/schema expectation now follows the structured atomic contract. |
| A | `tests/test_advocate_agents.py::test_agent_depends_only_on_the_provider_protocol` | Removed case fixture; secondary prompt/schema expectation now follows the structured atomic contract. |
| A | `tests/test_advocate_agents.py::test_request_carries_the_assigned_side_in_metadata` | Removed case fixture; secondary prompt/schema expectation now follows the structured atomic contract. |
| A | `tests/test_advocate_agents.py::test_request_embeds_the_trusted_context_as_camel_case` | Removed case fixture; secondary prompt/schema expectation now follows the structured atomic contract. |
| A | `tests/test_advocate_agents.py::test_user_prompt_wraps_the_context_and_forbids_recomputation` | Removed case fixture; secondary prompt/schema expectation now follows the structured atomic contract. |
| A | `tests/test_advocate_agents.py::test_system_prompt_contains_common_rules_side_role_and_output_schema` | Removed case fixture; secondary prompt/schema expectation now follows the structured atomic contract. |
| A | `tests/test_advocate_agents.py::test_prompt_does_not_mention_any_specific_vendor` | Removed case fixture; secondary prompt/schema expectation now follows the structured atomic contract. |
| A | `tests/test_advocate_agents.py::test_rider_and_driver_receive_different_side_prompts` | Removed case fixture; secondary prompt/schema expectation now follows the structured atomic contract. |
| A | `tests/test_advocate_agents.py::test_wrong_side_output_is_rejected_not_coerced` | Removed case fixture; secondary prompt/schema expectation now follows the structured atomic contract. |
| A | `tests/test_advocate_agents.py::test_malformed_json_raises_advocate_agent_error` | Removed case fixture; secondary prompt/schema expectation now follows the structured atomic contract. |
| A | `tests/test_advocate_agents.py::test_schema_violation_raises_advocate_agent_error` | Removed case fixture; secondary prompt/schema expectation now follows the structured atomic contract. |
| A | `tests/test_advocate_agents.py::test_provider_error_propagates_unchanged` | Removed case fixture; secondary prompt/schema expectation now follows the structured atomic contract. |
| B | `tests/test_advocate_agents.py::test_output_schema_omits_position_as_a_property` | position is now a valid ADVOCACY_POSITION proposition field, not a top-level Advocate or claim field. |
| A | `tests/test_advocate_api.py::test_all_four_cases_run_both_advocates` | Removed case fixture caused 404 and downstream missing response keys; execution metadata is now part of the response. |
| A | `tests/test_advocate_api.py::test_response_shape_matches_the_frontend_contract` | Removed case fixture caused 404 and downstream missing response keys; execution metadata is now part of the response. |
| A | `tests/test_advocate_api.py::test_judge_stage_is_reported_as_not_run` | Removed case fixture caused 404 and downstream missing response keys; execution metadata is now part of the response. |
| A | `tests/test_advocate_api.py::test_mock_mode_is_reported_honestly` | Removed case fixture caused 404 and downstream missing response keys; execution metadata is now part of the response. |
| A | `tests/test_advocate_api.py::test_verified_claims_only_reference_real_evidence` | Removed case fixture caused 404 and downstream missing response keys; execution metadata is now part of the response. |
| A | `tests/test_advocate_api.py::test_rejected_claim_ids_never_appear_in_verified_claims` | Removed case fixture caused 404 and downstream missing response keys; execution metadata is now part of the response. |
| A | `tests/test_advocate_api.py::test_real_mode_without_credentials_returns_503` | Removed case fixture caused 404 and downstream missing response keys; execution metadata is now part of the response. |
| A | `tests/test_advocate_api.py::test_existing_endpoints_are_unaffected` | Removed case fixture caused 404 and downstream missing response keys; execution metadata is now part of the response. |
| A | `tests/test_advocate_api.py::test_running_advocates_twice_is_idempotent_in_mock_mode` | Removed case fixture caused 404 and downstream missing response keys; execution metadata is now part of the response. |
| A | `tests/test_advocate_api.py::test_deliberate_hallucination_is_rejected_and_audited` | Removed case fixture caused 404 and downstream missing response keys; execution metadata is now part of the response. |
| A | `tests/test_advocate_context.py::test_route_case_exposes_correct_trusted_facts` | Removed cases and old distances/fares/messages; replaced using current structured DISP fixtures. |
| A | `tests/test_advocate_context.py::test_route_case_exposes_only_verified_conditions_and_supporting_evidence` | Removed cases and old distances/fares/messages; replaced using current structured DISP fixtures. |
| A | `tests/test_advocate_context.py::test_no_show_case_exposes_correct_trusted_facts` | Removed cases and old distances/fares/messages; replaced using current structured DISP fixtures. |
| A | `tests/test_advocate_context.py::test_context_contains_evidence_and_canonical_timeline` | Removed cases and old distances/fares/messages; replaced using current structured DISP fixtures. |
| A | `tests/test_advocate_context.py::test_context_contains_the_correct_policy_and_rules` | Removed cases and old distances/fares/messages; replaced using current structured DISP fixtures. |
| A | `tests/test_advocate_context.py::test_context_carries_policy_evaluation_rule_results` | Removed cases and old distances/fares/messages; replaced using current structured DISP fixtures. |
| A | `tests/test_advocate_context.py::test_context_flags_missing_and_conflicting_evidence` | Removed cases and old distances/fares/messages; replaced using current structured DISP fixtures. |
| A | `tests/test_advocate_context.py::test_context_does_not_leak_the_recommendation_or_any_answer` | Removed cases and old distances/fares/messages; replaced using current structured DISP fixtures. |
| A | `tests/test_advocate_context.py::test_context_has_no_recommendation_confidence_or_escalation_fields` | Removed cases and old distances/fares/messages; replaced using current structured DISP fixtures. |
| A | `tests/test_advocate_context.py::test_context_never_exposes_the_raw_calculation_input` | Removed cases and old distances/fares/messages; replaced using current structured DISP fixtures. |
| A | `tests/test_advocate_context.py::test_context_excludes_historical_and_fairness_sensitive_data` | Removed cases and old distances/fares/messages; replaced using current structured DISP fixtures. |
| A | `tests/test_advocate_orchestrator.py::test_both_advocates_complete_for_every_case[CASE-2026-1041]` | Removed cases; all eight fixtures now exercised. Atomic verification projects policy references only when verified and rejects unsupported references. |
| A | `tests/test_advocate_orchestrator.py::test_both_advocates_complete_for_every_case[CASE-2026-1042]` | Removed cases; all eight fixtures now exercised. Atomic verification projects policy references only when verified and rejects unsupported references. |
| A | `tests/test_advocate_orchestrator.py::test_both_advocates_complete_for_every_case[CASE-2026-1043]` | Removed cases; all eight fixtures now exercised. Atomic verification projects policy references only when verified and rejects unsupported references. |
| A | `tests/test_advocate_orchestrator.py::test_both_advocates_complete_for_every_case[CASE-2026-1044]` | Removed cases; all eight fixtures now exercised. Atomic verification projects policy references only when verified and rejects unsupported references. |
| A | `tests/test_advocate_orchestrator.py::test_pipeline_reports_judge_as_not_run[CASE-2026-1041]` | Removed cases; all eight fixtures now exercised. Atomic verification projects policy references only when verified and rejects unsupported references. |
| A | `tests/test_advocate_orchestrator.py::test_pipeline_reports_judge_as_not_run[CASE-2026-1042]` | Removed cases; all eight fixtures now exercised. Atomic verification projects policy references only when verified and rejects unsupported references. |
| A | `tests/test_advocate_orchestrator.py::test_pipeline_reports_judge_as_not_run[CASE-2026-1043]` | Removed cases; all eight fixtures now exercised. Atomic verification projects policy references only when verified and rejects unsupported references. |
| A | `tests/test_advocate_orchestrator.py::test_pipeline_reports_judge_as_not_run[CASE-2026-1044]` | Removed cases; all eight fixtures now exercised. Atomic verification projects policy references only when verified and rejects unsupported references. |
| A | `tests/test_advocate_orchestrator.py::test_every_verified_claim_cites_evidence_and_policy` | Removed cases; all eight fixtures now exercised. Atomic verification projects policy references only when verified and rejects unsupported references. |
| A | `tests/test_advocate_orchestrator.py::test_advocates_are_independent_and_may_disagree` | Removed cases; all eight fixtures now exercised. Atomic verification projects policy references only when verified and rejects unsupported references. |
| A | `tests/test_advocate_orchestrator.py::test_each_side_argues_its_own_case_independently` | Removed cases; all eight fixtures now exercised. Atomic verification projects policy references only when verified and rejects unsupported references. |
| A | `tests/test_advocate_orchestrator.py::test_driver_failure_does_not_break_the_rider` | Removed cases; all eight fixtures now exercised. Atomic verification projects policy references only when verified and rejects unsupported references. |
| A | `tests/test_advocate_orchestrator.py::test_rider_failure_does_not_break_the_driver` | Removed cases; all eight fixtures now exercised. Atomic verification projects policy references only when verified and rejects unsupported references. |
| A | `tests/test_advocate_orchestrator.py::test_total_provider_failure_degrades_but_does_not_raise` | Removed cases; all eight fixtures now exercised. Atomic verification projects policy references only when verified and rejects unsupported references. |
| A | `tests/test_advocate_orchestrator.py::test_failure_reason_never_leaks_credentials_or_internals` | Removed cases; all eight fixtures now exercised. Atomic verification projects policy references only when verified and rejects unsupported references. |
| A | `tests/test_advocate_orchestrator.py::test_verification_summary_totals_match_the_side_results` | Removed cases; all eight fixtures now exercised. Atomic verification projects policy references only when verified and rejects unsupported references. |
| A | `tests/test_advocate_orchestrator.py::test_orchestrator_does_not_mutate_the_case_or_analysis` | Removed cases; all eight fixtures now exercised. Atomic verification projects policy references only when verified and rejects unsupported references. |
| A | `tests/test_advocate_orchestrator.py::test_run_metadata_describes_mock_mode_without_model_or_secrets` | Removed cases; all eight fixtures now exercised. Atomic verification projects policy references only when verified and rejects unsupported references. |
| A | `tests/test_advocate_orchestrator.py::test_response_serialises_with_camel_case_keys` | Removed cases; all eight fixtures now exercised. Atomic verification projects policy references only when verified and rejects unsupported references. |
| A | `tests/test_advocate_orchestrator.py::test_orchestrator_is_deterministic_across_runs` | Removed cases; all eight fixtures now exercised. Atomic verification projects policy references only when verified and rejects unsupported references. |
| B | `tests/test_agent_request_contract.py::test_every_role_output_fits_the_1024_budget_with_margin` | Legacy 1024 output-headroom check, unversioned fabricated output, or previous schema identity hash. |
| B | `tests/test_agent_request_contract.py::test_a_trust_boundary_rejection_does_not_trigger_a_repair` | Legacy 1024 output-headroom check, unversioned fabricated output, or previous schema identity hash. |
| B | `tests/test_agent_request_contract.py::test_the_deterministic_engines_are_untouched` | Legacy 1024 output-headroom check, unversioned fabricated output, or previous schema identity hash. |
| B | `tests/test_benchmark.py::test_a_smoke_run_makes_exactly_four_calls` | Stub output omitted v2 atomic propositions and used evidence types unable to support its asserted facts. |
| B | `tests/test_benchmark.py::test_a_grounded_run_verifies_its_claims` | Stub output omitted v2 atomic propositions and used evidence types unable to support its asserted facts. |
| B | `tests/test_benchmark.py::test_a_hallucinated_evidence_id_is_rejected_by_the_real_verifier` | Stub output omitted v2 atomic propositions and used evidence types unable to support its asserted facts. |
| B | `tests/test_benchmark.py::test_cross_domain_fact_mistakes_are_counted` | Stub output omitted v2 atomic propositions and used evidence types unable to support its asserted facts. |
| B | `tests/test_benchmark.py::test_per_call_rejection_counters_agree_with_the_aggregate` | Stub output omitted v2 atomic propositions and used evidence types unable to support its asserted facts. |
| B | `tests/test_benchmark.py::test_one_skipped_model_does_not_stop_another` | Stub output omitted v2 atomic propositions and used evidence types unable to support its asserted facts. |
| A | `tests/test_claim_verification.py::test_hallucinated_evidence_id_is_rejected_and_never_verified` | Removed cases plus legacy asserted-fact-only outputs; tests now supply v2 atomic propositions and check the trusted projection. |
| A | `tests/test_claim_verification.py::test_hallucinated_short_evidence_id_is_also_rejected` | Removed cases plus legacy asserted-fact-only outputs; tests now supply v2 atomic propositions and check the trusted projection. |
| A | `tests/test_claim_verification.py::test_evidence_from_another_case_is_not_accepted` | Removed cases plus legacy asserted-fact-only outputs; tests now supply v2 atomic propositions and check the trusted projection. |
| A | `tests/test_claim_verification.py::test_well_formed_claim_is_verified` | Removed cases plus legacy asserted-fact-only outputs; tests now supply v2 atomic propositions and check the trusted projection. |
| A | `tests/test_claim_verification.py::test_fact_contradiction_is_hard_rejected` | Removed cases plus legacy asserted-fact-only outputs; tests now supply v2 atomic propositions and check the trusted projection. |
| A | `tests/test_claim_verification.py::test_float_values_within_tolerance_are_accepted` | Removed cases plus legacy asserted-fact-only outputs; tests now supply v2 atomic propositions and check the trusted projection. |
| A | `tests/test_claim_verification.py::test_boolean_fact_is_compared_strictly` | Removed cases plus legacy asserted-fact-only outputs; tests now supply v2 atomic propositions and check the trusted projection. |
| A | `tests/test_claim_verification.py::test_unknown_asserted_fact_name_is_rejected` | Removed cases plus legacy asserted-fact-only outputs; tests now supply v2 atomic propositions and check the trusted projection. |
| A | `tests/test_claim_verification.py::test_route_only_fact_is_unknown_for_no_show_case` | Removed cases plus legacy asserted-fact-only outputs; tests now supply v2 atomic propositions and check the trusted projection. |
| A | `tests/test_claim_verification.py::test_claim_without_evidence_reference_is_rejected` | Removed cases plus legacy asserted-fact-only outputs; tests now supply v2 atomic propositions and check the trusted projection. |
| A | `tests/test_claim_verification.py::test_claim_with_unknown_policy_ref_is_rejected` | Removed cases plus legacy asserted-fact-only outputs; tests now supply v2 atomic propositions and check the trusted projection. |
| A | `tests/test_claim_verification.py::test_claim_without_policy_ref_is_rejected` | Removed cases plus legacy asserted-fact-only outputs; tests now supply v2 atomic propositions and check the trusted projection. |
| A | `tests/test_claim_verification.py::test_duplicate_claim_ids_are_rejected_on_second_occurrence` | Removed cases plus legacy asserted-fact-only outputs; tests now supply v2 atomic propositions and check the trusted projection. |
| A | `tests/test_claim_verification.py::test_claim_resting_only_on_unverified_evidence_is_rejected` | Removed cases plus legacy asserted-fact-only outputs; tests now supply v2 atomic propositions and check the trusted projection. |
| A | `tests/test_claim_verification.py::test_claim_with_at_least_one_verified_evidence_passes_the_quality_check` | Removed cases plus legacy asserted-fact-only outputs; tests now supply v2 atomic propositions and check the trusted projection. |
| A | `tests/test_claim_verification.py::test_numeric_prose_without_asserted_facts_produces_a_warning` | Removed cases plus legacy asserted-fact-only outputs; tests now supply v2 atomic propositions and check the trusted projection. |
| A | `tests/test_claim_verification.py::test_prose_numbers_backed_by_asserted_facts_do_not_warn_numerically` | Removed cases plus legacy asserted-fact-only outputs; tests now supply v2 atomic propositions and check the trusted projection. |
| A | `tests/test_claim_verification.py::test_disputing_evidence_produces_a_warning_not_a_rejection` | Removed cases plus legacy asserted-fact-only outputs; tests now supply v2 atomic propositions and check the trusted projection. |
| A | `tests/test_claim_verification.py::test_summary_reports_counts_and_deduplicated_reasons` | Removed cases plus legacy asserted-fact-only outputs; tests now supply v2 atomic propositions and check the trusted projection. |
| A | `tests/test_provider_mock.py::test_mock_provider_returns_the_assigned_side_not_a_guessed_one` | Removed cases, old numeric expectations and omitted contractVersion. |
| A | `tests/test_provider_mock.py::test_mock_provider_is_deterministic_across_calls` | Removed cases, old numeric expectations and omitted contractVersion. |
| A | `tests/test_provider_mock.py::test_mock_provider_uses_only_trusted_context_values` | Removed cases, old numeric expectations and omitted contractVersion. |
| A | `tests/test_provider_mock.py::test_mock_provider_acknowledges_adverse_facts` | Removed cases, old numeric expectations and omitted contractVersion. |
| A | `tests/test_provider_mock.py::test_mock_provider_reports_different_outcomes_per_side_on_a_contestable_case` | Removed cases, old numeric expectations and omitted contractVersion. |
| A | `tests/test_provider_mock.py::test_mock_provider_emits_nothing_but_the_contract_fields` | Removed cases, old numeric expectations and omitted contractVersion. |
| B | `tests/test_provider_tokenhub.py::test_real_mode_runs_both_advocates_and_verifies_their_claims` | Stub output omitted v2 contract/propositions; current evidence support and request token ceiling were not reflected. |
| B | `tests/test_provider_tokenhub.py::test_real_mode_records_latency_tokens_and_claim_counts_per_side` | Stub output omitted v2 contract/propositions; current evidence support and request token ceiling were not reflected. |
| B | `tests/test_provider_tokenhub.py::test_hallucinated_evidence_from_a_real_model_is_still_rejected` | Stub output omitted v2 contract/propositions; current evidence support and request token ceiling were not reflected. |
| B | `tests/test_provider_tokenhub.py::test_contradicted_structured_fact_from_a_real_model_is_rejected` | Stub output omitted v2 contract/propositions; current evidence support and request token ceiling were not reflected. |
| B | `tests/test_provider_tokenhub.py::test_a_provider_failure_degrades_one_side_and_never_falls_back_to_mock` | Stub output omitted v2 contract/propositions; current evidence support and request token ceiling were not reflected. |
| B | `tests/test_provider_tokenhub.py::test_unusable_model_output_is_flagged_as_malformed_not_as_an_outage` | Stub output omitted v2 contract/propositions; current evidence support and request token ceiling were not reflected. |
| B | `tests/test_provider_tokenhub.py::test_a_malformed_response_that_repairs_cleanly_completes` | Stub output omitted v2 contract/propositions; current evidence support and request token ceiling were not reflected. |
| B | `tests/test_provider_tokenhub.py::test_truncated_output_is_flagged_and_keeps_its_token_metrics` | Stub output omitted v2 contract/propositions; current evidence support and request token ceiling were not reflected. |
| B | `tests/test_provider_tokenhub.py::test_configured_max_tokens_is_actually_sent` | Stub output omitted v2 contract/propositions; current evidence support and request token ceiling were not reflected. |
| B | `tests/test_provider_tokenhub.py::test_the_default_agent_request_asks_for_low_reasoning` | Stub output omitted v2 contract/propositions; current evidence support and request token ceiling were not reflected. |
| B | `tests/test_provider_tokenhub.py::test_a_configured_reasoning_effort_is_sent_by_the_agent` | Stub output omitted v2 contract/propositions; current evidence support and request token ceiling were not reflected. |
| B | `tests/test_workflow_state.py::test_scenario_h_malformed_output_is_repaired_exactly_once` | Scripted advocate responses used old unversioned claims and ungrounded evidence references. |
| B | `tests/test_workflow_state.py::test_scenario_h_a_trust_boundary_rejection_is_not_a_parse_failure` | Scripted advocate responses used old unversioned claims and ungrounded evidence references. |
| B | `tests/test_workflow_state.py::test_handoff_verification_splits_generated_claims_honestly` | Scripted advocate responses used old unversioned claims and ungrounded evidence references. |

## Final safety follow-up

After explicit approval, route evidence availability now treats required records marked Missing as unavailable. The retained failing regression and nine added E03/E04/E05 missing, absent and available cases pass. Final complete backend result: **864 passed / 0 failed**. The original 103-failure classification above remains unchanged. Frontend: **13 passed**, TypeScript and production build passed. All eight fixture outcomes and the saved DISP-005 MOCK_DEMO replay remain correct.
