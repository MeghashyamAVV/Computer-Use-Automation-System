from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass
from urllib.parse import urlparse

import yaml

from core.schema import ArtifactStep, RiskLevel


@dataclass
class AllowlistDecision:
    allowed: bool
    reason: str


class Allowlist:
    def __init__(self, config_path: str):
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        self.allowed_domains: list[str] = cfg.get("allowed_domains", [])
        self.allowed_path_patterns: list[str] = cfg.get("allowed_path_patterns", ["*"])
        self.allowed_actions: set[str] = set(cfg.get("allowed_actions", []))
        self.blocked_path_patterns: list[str] = cfg.get("blocked_path_patterns", [])

    def check_navigation(self, url: str) -> AllowlistDecision:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if self.allowed_domains and not any(
            host == d or host.endswith("." + d) for d in self.allowed_domains
        ):
            return AllowlistDecision(False, f"host '{host}' is not in allowed_domains {self.allowed_domains}")

        path = parsed.path or "/"
        for pattern in self.blocked_path_patterns:
            if fnmatch.fnmatch(path, pattern):
                return AllowlistDecision(False, f"path '{path}' matches blocked_path_patterns '{pattern}'")

        if self.allowed_path_patterns and not any(
            fnmatch.fnmatch(path, pattern) for pattern in self.allowed_path_patterns
        ):
            return AllowlistDecision(False, f"path '{path}' does not match any allowed_path_patterns")

        return AllowlistDecision(True, "ok")

    def check_action(self, action_type: str) -> AllowlistDecision:
        if self.allowed_actions and action_type not in self.allowed_actions:
            return AllowlistDecision(False, f"action '{action_type}' is not in allowed_actions {sorted(self.allowed_actions)}")
        return AllowlistDecision(True, "ok")


@dataclass
class RiskDecision:
    proceed: bool
    reason: str
    requires_escalation: bool = False


def evaluate_risk(step: ArtifactStep, artifact_review_status: str, allow_unreviewed: bool, confirm_risky: bool) -> RiskDecision:
    risk = step.risk.value if hasattr(step.risk, "value") else step.risk
    if risk != RiskLevel.HIGH.value and risk != "high":
        return RiskDecision(True, "not high-risk")

    if artifact_review_status == "approved":
        return RiskDecision(True, "artifact is approved for unattended replay")
    if confirm_risky:
        return RiskDecision(True, "operator passed --confirm-risky for this run")
    if allow_unreviewed:
        return RiskDecision(True, "caller explicitly allowed unreviewed high-risk execution")

    return RiskDecision(
        False,
        f"step '{step.step_id}' is HIGH risk and artifact review_status is '{artifact_review_status}' "
        f"(not 'approved'); refusing to execute unattended",
        requires_escalation=True,
    )
