from core.guardrails import Allowlist, evaluate_risk
from core.schema import ActionType, ArtifactStep, RiskLevel

ALLOWLIST_PATH = "config/allowlist.yaml"


def test_allowed_domain_and_path_passes():
    al = Allowlist(ALLOWLIST_PATH)
    d = al.check_navigation("http://127.0.0.1:5001/member/12345")
    assert d.allowed


def test_disallowed_domain_is_blocked():
    al = Allowlist(ALLOWLIST_PATH)
    d = al.check_navigation("http://evil.example.com/phish")
    assert not d.allowed


def test_blocked_path_pattern_wins_over_allowed_domain():
    al = Allowlist(ALLOWLIST_PATH)
    d = al.check_navigation("http://127.0.0.1:5001/admin/danger")
    assert not d.allowed


def test_action_allowlist():
    al = Allowlist(ALLOWLIST_PATH)
    assert al.check_action("click").allowed
    assert not al.check_action("execute_shell").allowed


def test_high_risk_step_blocked_when_artifact_draft_and_no_confirmation():
    step = ArtifactStep(step_id="s1", action=ActionType.CLICK, risk=RiskLevel.HIGH)
    decision = evaluate_risk(step, artifact_review_status="draft", allow_unreviewed=False, confirm_risky=False)
    assert not decision.proceed
    assert decision.requires_escalation


def test_high_risk_step_allowed_when_artifact_approved():
    step = ArtifactStep(step_id="s1", action=ActionType.CLICK, risk=RiskLevel.HIGH)
    decision = evaluate_risk(step, artifact_review_status="approved", allow_unreviewed=False, confirm_risky=False)
    assert decision.proceed


def test_high_risk_step_allowed_with_explicit_confirm_risky_flag():
    step = ArtifactStep(step_id="s1", action=ActionType.CLICK, risk=RiskLevel.HIGH)
    decision = evaluate_risk(step, artifact_review_status="draft", allow_unreviewed=False, confirm_risky=True)
    assert decision.proceed


def test_safe_step_never_blocked():
    step = ArtifactStep(step_id="s1", action=ActionType.EXTRACT, risk=RiskLevel.SAFE)
    decision = evaluate_risk(step, artifact_review_status="draft", allow_unreviewed=False, confirm_risky=False)
    assert decision.proceed
