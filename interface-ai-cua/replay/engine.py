from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from playwright.sync_api import sync_playwright

from core.guardrails import Allowlist, evaluate_risk
from core.logging_utils import RunLogger, new_run_id
from core.schema import Artifact, Checkpoint, CheckpointAssertion, KnownOutcome
from replay.locator import LocatorResolutionError, resolve_target

_TEMPLATE_RE = re.compile(r"\{\{\s*input\.([a-zA-Z0-9_]+)\s*\}\}")


class ReplayStatus(str, Enum):
    SUCCESS = "success"
    BUSINESS_OUTCOME = "business_outcome"
    RECOVERED = "recovered"
    FAILURE = "failure"


@dataclass
class ReplayError:
    step_id: str
    expected: str
    observed: str
    screenshot: Optional[str] = None


@dataclass
class ReplayResult:
    status: ReplayStatus
    run_id: str
    outputs: dict[str, Any] = field(default_factory=dict)
    outcome_id: Optional[str] = None
    error: Optional[ReplayError] = None
    recovered_conditions: list[str] = field(default_factory=list)
    strategy_log: list[dict[str, str]] = field(default_factory=list)  


def _substitute(value_template: Optional[str], input_values: dict[str, Any]) -> Optional[str]:
    if value_template is None:
        return None

    def repl(m):
        key = m.group(1)
        if key not in input_values:
            raise KeyError(f"artifact references input.{key} but it was not provided")
        return str(input_values[key])

    return _TEMPLATE_RE.sub(repl, value_template)


def _check_known_outcomes(page, last_status_holder: dict, known_outcomes: list[KnownOutcome]) -> Optional[KnownOutcome]:
    for outcome in known_outcomes:
        assertion = outcome.match_assertion
        assertion = assertion.value if hasattr(assertion, "value") else assertion
        if assertion == CheckpointAssertion.URL_MATCHES.value:
            if outcome.match_value in page.url:
                return outcome
        elif assertion == CheckpointAssertion.TEXT_PRESENT.value:
            try:
                body_text = page.inner_text("body")
            except Exception:
                body_text = ""
            if outcome.match_value.lower() in body_text.lower():
                return outcome
        elif assertion == CheckpointAssertion.STATUS_CODE.value:
            if str(last_status_holder.get("code")) == str(outcome.match_value):
                return outcome
    return None


def _check_checkpoint(page, last_status_holder: dict, checkpoint: Checkpoint) -> bool:
    assertion = checkpoint.assertion.value if hasattr(checkpoint.assertion, "value") else checkpoint.assertion
    if assertion == CheckpointAssertion.URL_MATCHES.value:
        return checkpoint.value in page.url
    if assertion == CheckpointAssertion.TEXT_PRESENT.value:
        try:
            return checkpoint.value.lower() in page.inner_text("body").lower()
        except Exception:
            return False
    if assertion == CheckpointAssertion.TEXT_ABSENT.value:
        try:
            return checkpoint.value.lower() not in page.inner_text("body").lower()
        except Exception:
            return True
    if assertion == CheckpointAssertion.STATUS_CODE.value:
        return str(last_status_holder.get("code")) == str(checkpoint.value)
    if assertion == CheckpointAssertion.ELEMENT_VISIBLE.value and checkpoint.target:
        try:
            resolve_target(page, checkpoint.target, timeout_ms=3000)
            return True
        except LocatorResolutionError:
            return False
    return False


def _attempt_recover_session_timeout(page, logger: RunLogger) -> bool:
    """Recoverable-condition example: the mock app redirects to /login?expired=1
    on session timeout. If we see that AND a service-account credential is
    configured out-of-band (never part of the artifact/input schema), log
    back in and let the caller retry the current step. Returns True if a
    re-login was performed."""
    if "/login" not in page.url:
        return False
    username = os.environ.get("CU_CONSOLE_USERNAME")
    password = os.environ.get("CU_CONSOLE_PASSWORD")
    if not username or not password:
        return False
    logger.log("recoverable_condition_detected", condition="session_timeout", url=page.url)
    page.get_by_label("Username").fill(username)
    page.get_by_label("Password").fill(password)
    page.get_by_role("button", name="Sign In").click()
    page.wait_for_load_state("networkidle", timeout=5000)
    logger.log("recovered", condition="session_timeout")
    return True


def replay_artifact(
    artifact: Artifact,
    input_values: dict[str, Any],
    *,
    allowlist_path: str = "config/allowlist.yaml",
    headless: bool = True,
    allow_unreviewed: bool = False,
    confirm_risky: bool = False,
    escalation_manager=None,
) -> ReplayResult:
    run_id = new_run_id("replay")
    logger = RunLogger(run_id)
    allowlist = Allowlist(allowlist_path)

    logger.log("replay_start", artifact_id=artifact.artifact_id, artifact_name=artifact.name,
               artifact_version=artifact.version, input_keys=list(input_values.keys()))

    for spec in artifact.input_schema:
        if spec.required and spec.name not in input_values:
            logger.log("input_validation_failed", missing=spec.name)
            return ReplayResult(
                status=ReplayStatus.FAILURE,
                run_id=run_id,
                error=ReplayError(step_id="<preflight>", expected=f"input '{spec.name}' provided",
                                   observed="missing"),
            )

    try:
        entry_url = _substitute(artifact.entry_url, input_values)
    except KeyError as e:
        return ReplayResult(status=ReplayStatus.FAILURE, run_id=run_id,
                             error=ReplayError(step_id="<preflight>", expected="valid entry_url template",
                                                observed=str(e)))

    nav_decision = allowlist.check_navigation(entry_url)
    if not nav_decision.allowed:
        logger.log("allowlist_blocked", url=entry_url, reason=nav_decision.reason)
        return ReplayResult(status=ReplayStatus.FAILURE, run_id=run_id,
                             error=ReplayError(step_id="<preflight>", expected="entry_url within allowlist",
                                                observed=nav_decision.reason))

    outputs: dict[str, Any] = {}
    recovered: list[str] = []
    strategy_log: list[dict[str, str]] = []
    last_status = {"code": None}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()

        def _on_response(response):
            try:
                if response.request.resource_type == "document":
                    last_status["code"] = response.status
            except Exception:
                pass

        page.on("response", _on_response)

        from core.auth import ensure_logged_in
        from urllib.parse import urlparse
        parsed = urlparse(entry_url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        ensure_logged_in(page, base)
        logger.log("session_bootstrap_ok")

        page.goto(entry_url)
        page.wait_for_load_state("networkidle", timeout=8000)

        outcome = _check_known_outcomes(page, last_status, artifact.known_outcomes)
        if outcome:
            screenshot = logger.save_screenshot(page, "known_outcome_preflight")
            logger.log("known_outcome_matched", outcome_id=outcome.outcome_id, at="preflight", screenshot=screenshot)
            browser.close()
            return ReplayResult(
                status=ReplayStatus.SUCCESS if outcome.is_success else ReplayStatus.BUSINESS_OUTCOME,
                run_id=run_id, outcome_id=outcome.outcome_id, outputs=outputs,
            )

        for step in artifact.steps:
            action = step.action.value if hasattr(step.action, "value") else step.action
            risk = step.risk.value if hasattr(step.risk, "value") else step.risk

            if risk == "high":
                risk_decision = evaluate_risk(step, artifact.review_status, allow_unreviewed, confirm_risky)
                logger.log("risk_check", step_id=step.step_id, proceed=risk_decision.proceed, reason=risk_decision.reason)
                if not risk_decision.proceed:
                    if escalation_manager is not None:
                        screenshot = logger.save_screenshot(page, f"{step.step_id}_risk_block")
                        resumed = escalation_manager.request_intervention(
                            page=page, logger=logger, goal=artifact.description, run_id=run_id,
                            step=step.step_id, reason=risk_decision.reason, screenshot_path=screenshot,
                        )
                        if resumed:
                            logger.log("risk_step_handled_by_human", step_id=step.step_id)
                            continue
                    screenshot = logger.save_screenshot(page, f"{step.step_id}_blocked")
                    browser.close()
                    return ReplayResult(
                        status=ReplayStatus.FAILURE, run_id=run_id,
                        error=ReplayError(step_id=step.step_id, expected="risk policy to allow execution",
                                           observed=risk_decision.reason, screenshot=screenshot),
                    )

            action_decision = allowlist.check_action(action)
            if not action_decision.allowed:
                screenshot = logger.save_screenshot(page, f"{step.step_id}_action_blocked")
                browser.close()
                return ReplayResult(
                    status=ReplayStatus.FAILURE, run_id=run_id,
                    error=ReplayError(step_id=step.step_id, expected="action within allowlist",
                                       observed=action_decision.reason, screenshot=screenshot),
                )

            try:
                value = _substitute(step.value_template, input_values)
            except KeyError as e:
                screenshot = logger.save_screenshot(page, f"{step.step_id}_template_error")
                browser.close()
                return ReplayResult(status=ReplayStatus.FAILURE, run_id=run_id,
                                     error=ReplayError(step_id=step.step_id, expected="resolvable value template",
                                                        observed=str(e), screenshot=screenshot))

            attempts = 1 + max(0, step.retry_on_timeout)
            last_exc = None
            for attempt in range(attempts):
                try:
                    if action == "navigate":
                        nav_decision = allowlist.check_navigation(value)
                        if not nav_decision.allowed:
                            raise PermissionError(nav_decision.reason)
                        page.goto(value)
                    elif action in ("click", "type", "select", "extract") and step.target:
                        resolved = resolve_target(page, step.target, timeout_ms=step.max_wait_ms)
                        strategy_log.append({"step_id": step.step_id, "strategy": resolved.strategy_used})
                        if action == "click":
                            resolved.playwright_locator.click()
                        elif action == "type":
                            resolved.playwright_locator.fill(value or "")
                        elif action == "select":
                            resolved.playwright_locator.select_option(value or "")
                        elif action == "extract" and step.output_key:
                            outputs[step.output_key] = resolved.playwright_locator.inner_text()
                    elif action == "wait_for":
                        page.wait_for_selector(f"text={value}", timeout=step.max_wait_ms)

                    page.wait_for_load_state("networkidle", timeout=5000)
                    last_exc = None
                    break
                except Exception as e:  # noqa: BLE001
                    last_exc = e
                    if _attempt_recover_session_timeout(page, logger):
                        recovered.append("session_timeout")
                        continue  # retry this same step now that we're logged back in
                    time.sleep(0.4 * (attempt + 1))
                    continue

            if last_exc is not None:
                screenshot = logger.save_screenshot(page, f"{step.step_id}_failed")
                logger.log("step_failed", step_id=step.step_id, action=action, error=str(last_exc), screenshot=screenshot)
                browser.close()
                return ReplayResult(
                    status=ReplayStatus.FAILURE, run_id=run_id,
                    error=ReplayError(
                        step_id=step.step_id,
                        expected=f"{action} on '{step.target.description if step.target else value}' to succeed",
                        observed=str(last_exc), screenshot=screenshot,
                    ),
                    recovered_conditions=recovered, strategy_log=strategy_log,
                )

            logger.log("step_ok", step_id=step.step_id, action=action)

            outcome = _check_known_outcomes(page, last_status, artifact.known_outcomes)
            if outcome:
                screenshot = logger.save_screenshot(page, f"{step.step_id}_known_outcome")
                logger.log("known_outcome_matched", outcome_id=outcome.outcome_id, step_id=step.step_id, screenshot=screenshot)
                browser.close()
                return ReplayResult(
                    status=ReplayStatus.SUCCESS if outcome.is_success else ReplayStatus.BUSINESS_OUTCOME,
                    run_id=run_id, outcome_id=outcome.outcome_id, outputs=outputs,
                    recovered_conditions=recovered, strategy_log=strategy_log,
                )

            if step.checkpoint and not _check_checkpoint(page, last_status, step.checkpoint):
                screenshot = logger.save_screenshot(page, f"{step.step_id}_checkpoint_failed")
                browser.close()
                return ReplayResult(
                    status=ReplayStatus.FAILURE, run_id=run_id,
                    error=ReplayError(step_id=step.step_id, expected=str(step.checkpoint.value),
                                       observed=f"url={page.url}", screenshot=screenshot),
                    recovered_conditions=recovered, strategy_log=strategy_log,
                )

        success_ok = _check_checkpoint(page, last_status, artifact.success_checkpoint)
        final_screenshot = logger.save_screenshot(page, "final")
        browser.close()

    if not success_ok:
        logger.log("success_checkpoint_failed")
        return ReplayResult(
            status=ReplayStatus.FAILURE, run_id=run_id,
            error=ReplayError(step_id="<success_checkpoint>", expected=str(artifact.success_checkpoint.value),
                               observed="checkpoint not satisfied at end of run", screenshot=final_screenshot),
            recovered_conditions=recovered, strategy_log=strategy_log,
        )

    status = ReplayStatus.RECOVERED if recovered else ReplayStatus.SUCCESS
    logger.log("replay_end", status=status.value, outputs=outputs, recovered_conditions=recovered)
    return ReplayResult(status=status, run_id=run_id, outputs=outputs, recovered_conditions=recovered, strategy_log=strategy_log)
