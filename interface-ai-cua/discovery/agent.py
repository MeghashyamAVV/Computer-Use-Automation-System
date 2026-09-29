from __future__ import annotations

import time
import uuid
import yaml
from dataclasses import dataclass
from typing import Optional

from playwright.sync_api import sync_playwright

from core.guardrails import Allowlist
from core.llm_client import LLMClient, AgentDecision
from core.logging_utils import RunLogger, new_run_id
from core.observation import observe
from core.schema import (
    Artifact,
    ArtifactStep,
    ActionType,
    Checkpoint,
    CheckpointAssertion,
    KnownOutcome,
    OutputSpec,
    ParamSpec,
    RiskLevel,
)
from core.target_builder import build_target

DEFAULT_MAX_STEPS = 15
DEFAULT_TIMEOUT_S = 180


@dataclass
class DiscoveryResult:
    success: bool
    artifact: Optional[Artifact]
    run_id: str
    reason: str


def _classify_risk(action: str, element_name: str, risky_keywords: list[str]) -> RiskLevel:
    if action == ActionType.NAVIGATE.value or action == "navigate":
        return RiskLevel.SAFE
    if action == ActionType.EXTRACT.value or action == "extract" or action == "wait_for":
        return RiskLevel.SAFE
    if action in ("type", "select"):
        return RiskLevel.LOW
    if action == "click":
        name_l = (element_name or "").lower()
        if any(k in name_l for k in risky_keywords):
            return RiskLevel.HIGH
        return RiskLevel.SAFE
    return RiskLevel.SAFE


def _value_template(value: str, input_values: dict[str, str]) -> str:
    for key, v in input_values.items():
        if v is not None and str(v) == str(value):
            return "{{input." + key + "}}"
    return value


def run_discovery(
    *,
    goal: str,
    entry_url: str,
    target_app: str,
    input_values: dict[str, str],
    param_specs: list[ParamSpec],
    output_specs: list[OutputSpec],
    known_outcomes: list[KnownOutcome],
    allowlist_path: str = "config/allowlist.yaml",
    llm_mode: str = "live",
    max_steps: int = DEFAULT_MAX_STEPS,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    headless: bool = True,
    escalation_manager=None,
    artifact_name: Optional[str] = None,
) -> DiscoveryResult:
    run_id = new_run_id("discovery")
    logger = RunLogger(run_id)
    allowlist = Allowlist(allowlist_path)
    with open(allowlist_path) as f:
        risky_keywords = yaml.safe_load(f).get("risky_click_keywords", [])

    llm = LLMClient(mode=llm_mode)
    logger.log("run_start", goal=goal, entry_url=entry_url, target_app=target_app, llm_mode=llm_mode)

    goal_for_llm = goal
    if output_specs:
        output_lines = [f'- {spec.name}: {spec.description or "(no description)"}' for spec in output_specs]
        goal_for_llm = (
            goal
            + "\n\nThis capability must extract the following output(s) before you call "
              'action="done" (use action="extract" with output_key set to the exact name shown):\n'
            + "\n".join(output_lines)
        )

    decision = allowlist.check_navigation(entry_url)
    if not decision.allowed:
        logger.log("allowlist_blocked", url=entry_url, reason=decision.reason)
        return DiscoveryResult(False, None, run_id, f"entry_url blocked by allowlist: {decision.reason}")

    executed_steps: list[ArtifactStep] = []
    history_lines: list[str] = []
    last_checkpoint: Optional[Checkpoint] = None
    success = False
    fail_reason = ""
    extracted_output_keys: set[str] = set()
    required_output_names = {spec.name for spec in output_specs}
    done_rejections = 0
    MAX_DONE_REJECTIONS = 2
    last_action_signature = None
    repeat_count = 0
    MAX_IDENTICAL_REPEATS = 2  
    prev_url: Optional[str] = None
    prev_element_signatures: set[tuple[str, str, str]] = set()
    prev_action_type: Optional[str] = None

    start_time = time.time()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()

        from core.auth import ensure_logged_in
        from urllib.parse import urlparse
        parsed = urlparse(entry_url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        ensure_logged_in(page, base)
        logger.log("session_bootstrap_ok")

        page.goto(entry_url)

        for step_num in range(max_steps):
            if time.time() - start_time > timeout_s:
                fail_reason = "discovery run exceeded timeout_s"
                logger.log("timeout", elapsed=time.time() - start_time)
                break

            obs = observe(page)
            screenshot_path = logger.save_screenshot(page, f"step{step_num}")
            logger.log("observation", step=step_num, url=obs.url, title=obs.title,
                       n_elements=len(obs.elements), screenshot=screenshot_path)

            current_element_signatures = {(el.role, el.name, el.value or "") for el in obs.elements}
            change_note = ""
            if step_num > 0:
                new_elements = sorted(current_element_signatures - prev_element_signatures)[:10]
                removed_elements = sorted(prev_element_signatures - current_element_signatures)[:10]
                url_changed = obs.url != prev_url
                read_only_last_action = prev_action_type in ("extract", "wait_for")
                if not url_changed and not new_elements and not removed_elements:
                    if read_only_last_action:
                        change_note = (
                            "NOTE: the page is unchanged, which is expected after a read-only "
                            f"action ({prev_action_type}).\n\n"
                        )
                    else:
                        change_note = (
                            "NOTE: nothing changed on the page since your last action -- it had NO "
                            "effect. Do not repeat it; the target may have been wrong, or you may need "
                            "a different approach.\n\n"
                        )
                else:
                    parts = []
                    if url_changed:
                        parts.append(f"the URL changed from {prev_url} to {obs.url}")
                    if new_elements:
                        parts.append(f"new elements appeared: {new_elements}")
                    if removed_elements:
                        parts.append(f"elements disappeared: {removed_elements}")
                    change_note = (
                        "NOTE: your last action WORKED -- " + "; ".join(parts) + ". "
                        "Base your next action on what is different now, don't repeat the same action.\n\n"
                    )
            observation_text = change_note + obs.to_llm_text()
            if change_note:
                logger.log("change_detected", step=step_num, note=change_note.strip())
            prev_url = obs.url
            prev_element_signatures = current_element_signatures

            try:
                agent_decision = llm.decide(goal_for_llm, observation_text, "\n".join(history_lines))
            except Exception as e:  
                logger.log("decide_error", step=step_num, error=str(e))
                history_lines.append(f"[{step_num}] (model response could not be parsed: {e})")
                continue

            signature = (agent_decision.action, agent_decision.target_index, agent_decision.value, agent_decision.output_key)
            if agent_decision.action not in ("done", "stuck") and signature == last_action_signature:
                repeat_count += 1
            else:
                repeat_count = 0
            last_action_signature = signature

            if repeat_count >= MAX_IDENTICAL_REPEATS:
                logger.log("repetition_detected", step=step_num, signature=str(signature), repeat_count=repeat_count)
                agent_decision = AgentDecision(
                    reasoning="[forced] identical action repeated without progress",
                    action="stuck",
                    stuck_reason=(
                        f"repeated the same action ({agent_decision.action} on target "
                        f"{agent_decision.target_index}) {repeat_count + 1} times in a row with no "
                        f"progress -- forcing escalation instead of looping further"
                    ),
                )

            logger.log("decision", step=step_num, action=agent_decision.action,
                       target_index=agent_decision.target_index, value=agent_decision.value,
                       reasoning=agent_decision.reasoning, output_key=agent_decision.output_key,
                       stuck_reason=agent_decision.stuck_reason)

            if agent_decision.action == "done":
                missing_outputs = required_output_names - extracted_output_keys
                if missing_outputs and done_rejections < MAX_DONE_REJECTIONS:
                    done_rejections += 1
                    logger.log("done_rejected_missing_outputs", step=step_num,
                               missing=sorted(missing_outputs), attempt=done_rejections,
                               done_had_output_key=agent_decision.output_key)
                    mistake_note = ""
                    if agent_decision.output_key:
                        mistake_note = (
                            f' You set output_key="{agent_decision.output_key}" on the done action itself -- '
                            f"that does NOT count as extracting it. You must call a separate "
                            f'action="extract" step on the actual element first.'
                        )
                    history_lines.append(
                        f"[{step_num}] you called done, but this goal requires extracting "
                        f"{sorted(missing_outputs)} first (declared in output_schema) and that has not "
                        f"happened yet.{mistake_note} Find the element containing that information in the "
                        f"CURRENT OBSERVATION and call action=\"extract\" with output_key set to the exact "
                        f"missing field name before calling done again."
                    )
                    continue
                if missing_outputs:
                    logger.log("done_accepted_with_missing_outputs", step=step_num, missing=sorted(missing_outputs))
                success = True
                break

            if agent_decision.action == "stuck":
                logger.log("stuck", reason=agent_decision.stuck_reason, screenshot=screenshot_path)
                if escalation_manager is not None:
                    resumed = escalation_manager.request_intervention(
                        page=page,
                        logger=logger,
                        goal=goal,
                        run_id=run_id,
                        step=step_num,
                        reason=agent_decision.stuck_reason or "agent reported stuck",
                        screenshot_path=screenshot_path,
                    )
                    if resumed:
                        history_lines.append(f"[{step_num}] (human intervention occurred; resuming automation)")
                        continue
                fail_reason = agent_decision.stuck_reason or "agent stuck, no escalation path available"
                break

            action_allowed = allowlist.check_action(agent_decision.action)
            if not action_allowed.allowed:
                fail_reason = f"action blocked by allowlist: {action_allowed.reason}"
                logger.log("allowlist_blocked_action", action=agent_decision.action, reason=action_allowed.reason)
                break

            if agent_decision.action in ("click", "type", "select", "extract") and agent_decision.target_index is None:
                logger.log("missing_target_index", step=step_num, action=agent_decision.action)
                history_lines.append(
                    f'[{step_num}] you called action="{agent_decision.action}" but did not set '
                    f"target_index. This action requires target_index to be the index number of the "
                    f"specific element from the CURRENT OBSERVATION you want to act on. Look at the "
                    f"observation again and try this action with target_index set."
                )
                continue

            extracted_text_for_history: Optional[str] = None

            try:
                pre_url = page.url
                el = None
                if agent_decision.target_index is not None:
                    if agent_decision.target_index >= len(obs.elements):
                        raise IndexError(f"target_index {agent_decision.target_index} out of range")
                    el = obs.elements[agent_decision.target_index]

                if agent_decision.action == "navigate":
                    nav_decision = allowlist.check_navigation(agent_decision.value)
                    if not nav_decision.allowed:
                        raise PermissionError(f"navigation blocked: {nav_decision.reason}")
                    page.goto(agent_decision.value)
                    target = None
                elif agent_decision.action in ("click", "type", "select", "extract"):
                    target = build_target(el, description=f"{el.role} \"{el.name}\"")
                    from replay.locator import resolve_target  

                    resolved = resolve_target(page, target, timeout_ms=5000)
                    if agent_decision.action == "click":
                        resolved.playwright_locator.click()
                    elif agent_decision.action == "type":
                        resolved.playwright_locator.fill(agent_decision.value or "")
                    elif agent_decision.action == "select":
                        resolved.playwright_locator.select_option(agent_decision.value or "")
                    elif agent_decision.action == "extract":
                        extracted_text = resolved.playwright_locator.inner_text()
                        extracted_text_for_history = extracted_text
                        logger.log("extracted", step=step_num, output_key=agent_decision.output_key, text=extracted_text)
                        if agent_decision.output_key:
                            extracted_output_keys.add(agent_decision.output_key)
                elif agent_decision.action == "wait_for":
                    page.wait_for_selector(f"text={agent_decision.value}", timeout=8000)
                    target = None
                else:
                    raise ValueError(f"unknown action {agent_decision.action}")

                page.wait_for_load_state("networkidle", timeout=5000)
            except Exception as e:  # noqa: BLE001
                logger.log("action_error", step=step_num, action=agent_decision.action, error=str(e))
                fail_reason = f"action '{agent_decision.action}' failed at step {step_num}: {e}"
                break

            post_url = page.url
            step_checkpoint = None
            if post_url != pre_url:
                step_checkpoint = Checkpoint(assertion=CheckpointAssertion.URL_MATCHES, value=post_url)
                last_checkpoint = step_checkpoint

            if agent_decision.action != "navigate" and el is not None:
                risk = _classify_risk(agent_decision.action, el.name, risky_keywords)
                value_tpl = _value_template(agent_decision.value, input_values) if agent_decision.value else None
                artifact_step = ArtifactStep(
                    step_id=f"step_{len(executed_steps) + 1}",
                    action=ActionType(agent_decision.action),
                    target=target,
                    value_template=value_tpl,
                    output_key=agent_decision.output_key,
                    risk=risk,
                    checkpoint=step_checkpoint,
                )
            else:
                artifact_step = ArtifactStep(
                    step_id=f"step_{len(executed_steps) + 1}",
                    action=ActionType(agent_decision.action),
                    target=None,
                    value_template=agent_decision.value if agent_decision.action == "navigate" else None,
                    risk=RiskLevel.SAFE,
                    checkpoint=step_checkpoint,
                )
            executed_steps.append(artifact_step)
            history_note = f"[{step_num}] {agent_decision.action} -> {agent_decision.reasoning}"
            if agent_decision.action == "extract" and extracted_text_for_history is not None:
                preview = extracted_text_for_history[:150].replace("\n", " ")
                history_note += f' -- extracted output_key="{agent_decision.output_key}" = "{preview}"'
            history_lines.append(history_note)
            prev_action_type = agent_decision.action

        final_screenshot = logger.save_screenshot(page, "final")
        final_url = page.url
        browser.close()

    logger.log("run_end", success=success, fail_reason=fail_reason, n_steps=len(executed_steps),
               final_url=final_url, final_screenshot=final_screenshot)

    if not success:
        return DiscoveryResult(False, None, run_id, fail_reason or "goal not completed within max_steps")

    if last_checkpoint is None:
        last_checkpoint = Checkpoint(assertion=CheckpointAssertion.URL_MATCHES, value=final_url)

    artifact = Artifact(
        artifact_id=str(uuid.uuid4()),
        name=artifact_name or goal[:60],
        description=goal,
        target_app=target_app,
        entry_url=entry_url,
        input_schema=param_specs,
        output_schema=output_specs,
        steps=executed_steps,
        success_checkpoint=last_checkpoint,
        known_outcomes=known_outcomes,
        created_from_run_id=run_id,
    )
    return DiscoveryResult(True, artifact, run_id, "goal completed")
