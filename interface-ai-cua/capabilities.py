from core.schema import CheckpointAssertion, KnownOutcome, OutputSpec, ParamSpec

TARGET_APP = "cu_console"


def base_url(port: int) -> str:
    return f"http://127.0.0.1:{port}"


LOOKUP_BALANCE = dict(
    name="lookup_member_savings_balance",
    param_specs=[
        ParamSpec(name="member_id", type="string", required=True, description="Member ID to look up"),
    ],
    output_specs=[
        OutputSpec(name="savings_balance_text", type="string", description="Savings balance as displayed"),
    ],
    known_outcomes=[
        KnownOutcome(
            outcome_id="member_not_found",
            match_assertion=CheckpointAssertion.TEXT_PRESENT,
            match_value="No member found",
            description="No record exists for the given member ID -- a legitimate result, not a failure.",
            is_success=False,
        ),
        KnownOutcome(
            outcome_id="permission_denied",
            match_assertion=CheckpointAssertion.TEXT_PRESENT,
            match_value="Access Denied",
            description="The operator's session is not permitted to view this member's balances.",
            is_success=False,
        ),
    ],
)

OPEN_SUB_ACCOUNT = dict(
    name="open_sub_account",
    param_specs=[
        ParamSpec(name="member_id", type="string", required=True, description="Member ID to open the sub-account for"),
        ParamSpec(name="account_type", type="string", required=True, description="youth_savings | holiday_club | money_market"),
        ParamSpec(name="initial_deposit", type="string", required=True, description="Initial deposit amount in dollars, e.g. '100'"),
    ],
    output_specs=[
        OutputSpec(name="account_number", type="string", description="Newly created sub-account number"),
    ],
    known_outcomes=[
        KnownOutcome(
            outcome_id="member_not_found",
            match_assertion=CheckpointAssertion.TEXT_PRESENT,
            match_value="No member record exists",
            description="No record exists for the given member ID.",
            is_success=False,
        ),
        KnownOutcome(
            outcome_id="deposit_below_minimum",
            match_assertion=CheckpointAssertion.TEXT_PRESENT,
            match_value="must be at least",
            description="The submitted initial deposit was below the $50 minimum -- a validation error, not a crash.",
            is_success=False,
        ),
    ],
)
