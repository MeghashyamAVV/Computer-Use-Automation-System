# REPORT

# 1. Architecture

Four decoupled pieces connected by one artifact:

    discovery/agent.py -> Artifact (core/schema.py) -> replay/engine.py

    Shared: core.llm_client (only LLM caller, discovery only), core.guardrails,
    core.observation, core.auth, escalation/manager.py

Key decisions:

- Single process, sync Playwright, no queues or services. A CLI with three subcommands is the whole interface. The seam for a future service is the artifact boundary.
- discovery/ and replay/ never import each other. Replay has zero dependency on core.llm_client or discovery.agent, which is the concrete guarantee that replay runs without the LLM.
- Session bootstrap is outside the artifact. Both modes call core.auth.ensure_logged_in() first, with credentials from the environment, never from the goal text or the artifact.
- Perception is shared. core/observation.py builds an accessibility-tree-style list of elements (role, accessible name, value) across every frame. The LLM and replay/locator.py use the same role+name vocabulary.
- Guardrails and escalation are shared services. Allowlist and evaluate_risk are called identically from discovery and replay. escalation/manager.py exposes one method, request_intervention(...).

Trade-off: step-classification heuristics (risk keywords, checkpoint inference from URL changes) are simple and somewhat app-specific. Remaining depth went into the artifact schema and replay error handling.

# 2. Artifact schema

An artifact (core/schema.py: Artifact) is a contract, not a transcript, rebuilt from the elements the agent actually interacted with. It contains:

- Ordered steps (ArtifactStep): action type, target, value_template (literal or {{input.<param>}}), optional checkpoint, risk level.
- Target identification with a fallback chain (ResolvedTarget): a primary locator plus ordered fallbacks: role+accessible-name, then label, then visible text, then structural CSS path. Nothing depends on an id attribute.
- Typed inputs (ParamSpec, with a sensitive flag for redaction) and typed outputs (OutputSpec).
- A success Checkpoint plus a separate known_outcomes list (KnownOutcome): patterns that mean a legitimate business result rather than success or crash.
- review_status (draft | approved): gates unattended execution of HIGH-risk steps.

known_outcomes and the input/output schema are curated by a developer in capabilities.py, not inferred by the LLM. Discovery proves the flow is executable and produces steps and locators.

# 3. Determinism and error handling

Determinism comes from three things:

1. Fallback-chain locator resolution (replay/locator.py): primary locator, then each fallback in order, logging which strategy resolved.
2. Bounded retries (step.retry_on_timeout): a fixed number of retries with short backoff for transient slowness, no LLM involved.
3. Per-step and success checkpoints: after a URL-changing step, the checkpoint is verified before continuing.

ReplayStatus has four states:

- SUCCESS: checkpoint met, outputs returned.
- BUSINESS_OUTCOME: a KnownOutcome matched (member not found, permission denied, deposit below minimum). Reported with an outcome_id, not raised.
- RECOVERED: a recoverable condition was handled and the run still succeeded. Implemented for session timeout: redirect to /login is detected, the engine re-authenticates with environment credentials and retries the step.
- FAILURE: hard failure carrying step_id, expected, observed, and a screenshot (ReplayError).

ReplayResult.strategy_log records which strategy resolved each step. A run where every step fell back to CSS is a visible drift signal.

# 4. Heterogeneity and multi-tenant

Surface abstraction. The seam is core/observation.py plus core/target_builder.py and replay/locator.py. Artifacts store only role/name/frame-path locators, never raw DOM, screenshots, or coordinates.

- Frameset-heavy apps: observation already walks frame.child_frames recursively and records frame_path, so no schema change is needed.
- Native desktop apps: add a LocatorStrategy (for example AUTOMATION_ID or a UIA/AX role+name pair) and desktop implementations of observe() and resolve_target() behind the same signatures. The agent loop and replay engine do not change.

Multi-tenant reuse. Do not re-record per tenant:

- Canonicalize tenant-specific values (entry_url, branded labels, route segments) into {{input.*}} or {{tenant.*}} variables so one artifact serves many tenants.
- Use a sparse per-tenant override layer: replay checks the tenant override first, then the base locator. Drift stays isolated to the elements that differ.
- Aggregate strategy_log per tenant and version. A tenant increasingly falling back to CSS locators is a review candidate before it becomes a failure.

Override storage and the drift dashboard are not built; the schema needs no change to support them.

# 5. Escalation and handoff

Detect. Two triggers: the discovery agent's action="stuck" (with a required stuck_reason), and replay's guardrail refusing an unreviewed HIGH-risk step (core/guardrails.py: evaluate_risk). Both call EscalationManager.request_intervention(...).

Take control. Playwright's page is only touched by the automation thread, so control transfer is a command relay, not a thread handoff. request_intervention blocks the automation thread on a per-request queue.Queue. A background Flask operator console shows the latest screenshot and reason and accepts a manual action (click/type/navigate by role+name). The automation thread executes it against the same page, then captures a fresh screenshot. Every manual action is logged (intervention_manual_action) in the same run log.

Hand back. Clicking Resume pushes {"type": "resume"}; request_intervention returns True and the caller continues.

Scope. The operator UI is a single unstyled Flask page with no auth and no live video. The pause, cede-control, act-on-live-session, resume mechanism is real.

# 6. Safety

- Allowlist (config/allowlist.yaml, core.guardrails.Allowlist): blocks navigation outside allowed domains and paths, and any action type not explicitly allowed. Checked before every navigation and action in both modes.
- Risk tiering (safe / low / high): assigned in discovery by a keyword heuristic over the clicked element's accessible name (risky_click_keywords in config/allowlist.yaml). HIGH-risk steps are refused in unattended replay unless the artifact is review_status: approved, or --confirm-risky is passed, or --allow-unreviewed is passed.
- No secrets or raw sensitive data persisted: core/redact.py runs on every log record (core/logging_utils.py: RunLogger.log), removing declared sensitive fields and sweeping free text for account-number and credential patterns. Credentials never enter the artifact or goal text.

Limits: risk classification is a keyword heuristic, so a differently worded "Open Account" button could be classed safe. The redaction sweep is best-effort. Neither replaces human review before setting review_status: approved.

# 7. Cuts

- Multi-tenant override storage and drift dashboard: designed, not built. No second tenant to validate against.
- Desktop/OS-level automation: designed (Section 4), not implemented.
- Confidence scoring and automatic draft-to-approved promotion: the review_status gate is enforced, but promotion is manual.
- Assisted fallback (bounded LLM recovery on replay failure): not implemented, to keep "replay never calls the LLM" absolute.
- Richer risk classification: currently button-text keywords; a stronger version would consider the HTTP method and side effects.
- Operator console polish: no auth, no live video, one element per manual action.
- lookup_member_savings_balance surfaces the balance directly in search results. Across several discovery runs with a small local model, the recurring failure was navigation reasoning: extracting from the wrong element, or repeating a click that had already succeeded. I removed the extra hop for this one capability so discovery stays tractable for a free local model. The member-detail page, its iframe balance panel, and the search-detail-action pattern are still exercised by open_sub_account (form, review, confirm) and by the frame-crossing logic in core/observation.py and core/target_builder.py, covered by test_target_builder.py.

With more time, in order:

1. Validate the multi-tenant override model against a second mock target sharing the same vendor look.
2. Add an approve-artifact CLI command plus replay-count and failure-rate fields.
3. Richer risk classification.