import json

from core.schema import (
    ActionType,
    Artifact,
    ArtifactStep,
    Checkpoint,
    CheckpointAssertion,
    KnownOutcome,
    Locator,
    LocatorStrategy,
    OutputSpec,
    ParamSpec,
    ResolvedTarget,
    RiskLevel,
)


def _sample_artifact() -> Artifact:
    target = ResolvedTarget(
        description='Member ID field',
        primary=Locator(strategy=LocatorStrategy.ROLE_NAME, value="Member ID", role="textbox"),
        fallbacks=[
            Locator(strategy=LocatorStrategy.LABEL, value="Member ID"),
            Locator(strategy=LocatorStrategy.CSS, value="input[name=member_id]"),
        ],
    )
    step = ArtifactStep(
        step_id="step_1",
        action=ActionType.TYPE,
        target=target,
        value_template="{{input.member_id}}",
        risk=RiskLevel.LOW,
    )
    return Artifact(
        artifact_id="test-artifact-1",
        name="lookup_member_savings_balance",
        description="Look up a member and read their savings balance",
        target_app="cu_console",
        entry_url="http://127.0.0.1:5001/search",
        input_schema=[ParamSpec(name="member_id", type="string")],
        output_schema=[OutputSpec(name="savings_balance_text", type="string")],
        steps=[step],
        success_checkpoint=Checkpoint(assertion=CheckpointAssertion.TEXT_PRESENT, value="Savings"),
        known_outcomes=[
            KnownOutcome(
                outcome_id="member_not_found",
                match_assertion=CheckpointAssertion.TEXT_PRESENT,
                match_value="No member found",
                description="legit business outcome",
            )
        ],
        created_from_run_id="discovery_run_1",
    )


def test_artifact_round_trips_through_json():
    artifact = _sample_artifact()
    raw = artifact.model_dump_json()
    reloaded = Artifact.model_validate_json(raw)
    assert reloaded.artifact_id == artifact.artifact_id
    assert reloaded.steps[0].value_template == "{{input.member_id}}"
    assert reloaded.steps[0].target.primary.role == "textbox"
    assert reloaded.known_outcomes[0].outcome_id == "member_not_found"


def test_artifact_is_plain_json_serializable():
    artifact = _sample_artifact()
    data = json.loads(artifact.model_dump_json())
    assert data["review_status"] == "draft"
    assert data["version"] == 1


def test_step_defaults_are_safe():
    artifact = _sample_artifact()
    # default risk on a freshly constructed step without explicit risk should be SAFE
    step = ArtifactStep(step_id="s", action=ActionType.EXTRACT)
    assert step.risk == RiskLevel.SAFE or step.risk == "safe"
