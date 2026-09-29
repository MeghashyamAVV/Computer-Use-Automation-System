from __future__ import annotations

import argparse
import glob
import json
import os
import sys

from dotenv import load_dotenv

load_dotenv()

from capabilities import LOOKUP_BALANCE, OPEN_SUB_ACCOUNT, TARGET_APP, base_url  # noqa: E402
from core.schema import Artifact  # noqa: E402

ARTIFACT_STORE = "artifacts/store"

CAPABILITIES = {
    "lookup_balance": LOOKUP_BALANCE,
    "open_sub_account": OPEN_SUB_ACCOUNT,
}

DEFAULT_GOALS = {
    "lookup_balance": "Look up member {member_id} and read their current savings balance.",
    "open_sub_account": (
        "Open a new {account_type} sub-account for member {member_id} with an initial deposit "
        "of ${initial_deposit} and reach the confirmation screen."
    ),
}

ENTRY_PATHS = {
    "lookup_balance": "/search",
    "open_sub_account": "/member/{member_id}/sub-account/new",
}


def _parse_inputs(pairs: list[str]) -> dict[str, str]:
    out = {}
    for p in pairs or []:
        if "=" not in p:
            raise SystemExit(f"--input must be KEY=VALUE, got: {p}")
        k, v = p.split("=", 1)
        out[k] = v
    return out


def _target_app_port() -> int:
    return int(os.environ.get("TARGET_APP_PORT", "5001"))


def cmd_run_goal(args):
    cap = CAPABILITIES[args.capability]
    input_values = _parse_inputs(args.input)

    missing = [s.name for s in cap["param_specs"] if s.required and s.name not in input_values]
    if missing:
        raise SystemExit(f"missing required --input values for capability '{args.capability}': {missing}")

    goal_text = args.goal or DEFAULT_GOALS[args.capability].format(**input_values)
    entry_path = ENTRY_PATHS[args.capability].format(**input_values)
    entry_url = base_url(_target_app_port()) + entry_path

    escalation_manager = None
    if args.escalate:
        from escalation.manager import EscalationManager

        escalation_manager = EscalationManager(operator_port=int(os.environ.get("OPERATOR_PORT", "5050")))

    from discovery.agent import run_discovery

    result = run_discovery(
        goal=goal_text,
        entry_url=entry_url,
        target_app=TARGET_APP,
        input_values=input_values,
        param_specs=cap["param_specs"],
        output_specs=cap["output_specs"],
        known_outcomes=cap["known_outcomes"],
        llm_mode="mock" if args.mock else ("ollama" if args.ollama else "live"),
        headless=not args.headed,
        escalation_manager=escalation_manager,
        artifact_name=cap["name"],
        max_steps=args.max_steps,
        timeout_s=args.timeout,
    )

    print(f"\nrun_id: {result.run_id}")
    print(f"success: {result.success}")
    print(f"reason: {result.reason}")

    if result.success and result.artifact:
        os.makedirs(ARTIFACT_STORE, exist_ok=True)
        out_path = os.path.join(ARTIFACT_STORE, f"{cap['name']}_v{result.artifact.version}.json")
        with open(out_path, "w") as f:
            f.write(result.artifact.model_dump_json(indent=2))
        print(f"artifact saved: {out_path}")
        print(f"artifact_id: {result.artifact.artifact_id}")
        print(f"\nReplay it with:\n  python cli.py replay --artifact {out_path} " +
              " ".join(f'--input {k}={v}' for k, v in input_values.items()))
    else:
        sys.exit(1)


def _load_artifact(ref: str) -> Artifact:
    path = ref
    if not os.path.exists(path):
        # try resolving by artifact_id or by name across the store
        candidates = glob.glob(os.path.join(ARTIFACT_STORE, "*.json"))
        for c in candidates:
            with open(c) as f:
                data = json.load(f)
            if data.get("artifact_id") == ref or data.get("name") == ref:
                path = c
                break
        else:
            raise SystemExit(f"could not find artifact matching '{ref}' in {ARTIFACT_STORE}")
    with open(path) as f:
        return Artifact.model_validate_json(f.read())


def cmd_replay(args):
    artifact = _load_artifact(args.artifact)
    input_values = _parse_inputs(args.input)

    escalation_manager = None
    if args.escalate:
        from escalation.manager import EscalationManager

        escalation_manager = EscalationManager(operator_port=int(os.environ.get("OPERATOR_PORT", "5050")))

    from replay.engine import replay_artifact

    result = replay_artifact(
        artifact,
        input_values,
        headless=not args.headed,
        allow_unreviewed=args.allow_unreviewed,
        confirm_risky=args.confirm_risky,
        escalation_manager=escalation_manager,
    )

    print(f"\nrun_id: {result.run_id}")
    print(f"status: {result.status.value}")
    if result.outcome_id:
        print(f"outcome_id: {result.outcome_id}")
    if result.outputs:
        print(f"outputs: {json.dumps(result.outputs, indent=2)}")
    if result.recovered_conditions:
        print(f"recovered_conditions: {result.recovered_conditions}")
    if result.error:
        print(f"error:\n  step_id: {result.error.step_id}\n  expected: {result.error.expected}\n  observed: {result.error.observed}")
        if result.error.screenshot:
            print(f"  screenshot: {result.error.screenshot}")
        sys.exit(2)


def cmd_list_artifacts(args):
    for path in sorted(glob.glob(os.path.join(ARTIFACT_STORE, "*.json"))):
        with open(path) as f:
            data = json.load(f)
        print(f"{data['name']} (v{data['version']}) -- {data['artifact_id']}")
        print(f"  file: {path}")
        print(f"  review_status: {data.get('review_status')}")
        print(f"  inputs: {[p['name'] for p in data.get('input_schema', [])]}")
        print(f"  outputs: {[o['name'] for o in data.get('output_schema', [])]}")
        print()


def build_parser():
    parser = argparse.ArgumentParser(description="Computer-use automation CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run-goal", help="Run a goal-driven discovery session and save the resulting artifact")
    p_run.add_argument("--capability", choices=list(CAPABILITIES), required=True)
    p_run.add_argument("--input", action="append", help="KEY=VALUE, repeatable")
    p_run.add_argument("--goal", help="Override the default natural-language goal text")
    p_run.add_argument("--mock", action="store_true", help="Use the rule-based mock LLM instead of a real API call")
    p_run.add_argument("--ollama", action="store_true", help="Use a local model via Ollama instead of the Anthropic API (set OLLAMA_MODEL in .env)")
    p_run.add_argument("--headed", action="store_true", help="Show the browser window instead of running headless")
    p_run.add_argument("--escalate", action="store_true", help="Enable human-escalation if the agent gets stuck")
    p_run.add_argument("--max-steps", type=int, default=15)
    p_run.add_argument("--timeout", type=int, default=180)
    p_run.set_defaults(func=cmd_run_goal)

    p_replay = sub.add_parser("replay", help="Deterministically replay a saved artifact")
    p_replay.add_argument("--artifact", required=True, help="Path to artifact JSON, or its artifact_id/name")
    p_replay.add_argument("--input", action="append", help="KEY=VALUE, repeatable")
    p_replay.add_argument("--headed", action="store_true")
    p_replay.add_argument("--escalate", action="store_true", help="Enable human-escalation on hard failures / risk blocks")
    p_replay.add_argument("--confirm-risky", action="store_true", help="Explicitly confirm execution of HIGH-risk steps for this run")
    p_replay.add_argument("--allow-unreviewed", action="store_true", help="Allow HIGH-risk steps even though the artifact is still 'draft'")
    p_replay.set_defaults(func=cmd_replay)

    p_list = sub.add_parser("list-artifacts", help="List saved artifacts")
    p_list.set_defaults(func=cmd_list_artifacts)

    return parser


if __name__ == "__main__":
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)
