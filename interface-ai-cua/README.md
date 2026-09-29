# Computer-Use Automation System

An LLM discovers how to complete a goal inside a legacy, no-API back-office web app, and the run is saved as a typed, versioned artifact that replays deterministically with no model in the loop. Design write-up: /REPORT.md

The target is app/, a small mock "CU-Console" core-banking screen (table layout, no test IDs, an iframe, toggleable failure modes).

# Setup

Requires Python 3.10+.

    git clone <this-repo-url>
    cd interface-ai-cua
    python3 -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    python -m playwright install chromium
    cp .env.example .env

In .env, set CU_CONSOLE_USERNAME and CU_CONSOLE_PASSWORD (any non-empty values work) and, for a genuine discovery run, ANTHROPIC_API_KEY.

# Start the target app

Leave this running in a separate terminal:

    python -m app.server

It serves on http://127.0.0.1:5001.

# Usage

Discovery run (needs ANTHROPIC_API_KEY):

    python cli.py run-goal --capability lookup_balance --input member_id=12345

It saves the artifact and prints a replay command. Logs and screenshots go to evidence/logs/<run_id>.jsonl and evidence/screenshots/.

Replay, no LLM:

    python cli.py replay --artifact artifacts/store/lookup_member_savings_balance_v1.json --input member_id=12345

Business outcome (legitimate result, not a crash):

    python cli.py replay --artifact artifacts/store/lookup_member_savings_balance_v1.json --input member_id=99999
    # status: business_outcome, outcome_id: member_not_found

Write flow (multi-step form plus a HIGH-risk step):

    python cli.py run-goal --capability open_sub_account --input member_id=12345 --input account_type=youth_savings --input initial_deposit=100

    python cli.py replay --artifact artifacts/store/open_sub_account_v1.json --input member_id=12345 --input account_type=youth_savings --input initial_deposit=100 --confirm-risky

The final "Confirm & Open Account" click is risk: high. Without --confirm-risky and an artifact with review_status: approved, replay refuses to run it unattended (core/guardrails.py).

Human escalation: add --escalate to either command. When the agent is stuck or replay hits a blocked HIGH-risk step, it prints a URL like http://127.0.0.1:5050/console/<request_id>. Open it to see the live screenshot, issue a manual action (click/type/navigate by role+name), or click Resume automation.

List saved artifacts:

    python cli.py list-artifacts

# Local model (Ollama)

Run discovery against a local model with no API key:

    ollama pull llama3.1
    ollama serve
    python cli.py run-goal --capability lookup_balance --input member_id=12345 --ollama

Set OLLAMA_HOST and OLLAMA_MODEL in .env. Works well with tool calling: llama3.1, llama3.2, qwen2.5, mistral-nemo. Smaller models get stuck more often; --escalate covers that.

# Without live services

- --mock on run-goal uses a rule-based stand-in for the LLM to exercise artifacts, logging, guardrails and replay. It does not replace the genuine run required in evidence/.
- Replay never needs an API key.
- Set SESSION_TTL_SECONDS=20 in .env and pause mid-replay to trigger session-timeout recovery.

# Layout

    app/              mock legacy target app (Flask)
    core/             schema, guardrails, redaction, perception, LLM client, auth
    discovery/        LLM-driven agent loop
    replay/           deterministic replay engine, locator resolution
    escalation/       human handoff manager and operator console
    capabilities.py   input/output schema and known_outcomes per goal
    cli.py            run-goal / replay / list-artifacts
    artifacts/store/  saved capability artifacts (JSON)
    evidence/         logs and screenshots from real runs
    tests/            browser-free unit tests

# Tests

    pytest tests/ -q