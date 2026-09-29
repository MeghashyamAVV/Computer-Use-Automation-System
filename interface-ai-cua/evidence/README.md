# Evidence

Run artifacts go here: logs/<run_id>.jsonl (structured, redacted) and screenshots/*.png (one per step, plus a richer capture on failure).

# Status

Empty. The sandbox that built this repo had no access to the Playwright browser CDN, so no browser could launch and no real run happened. No logs or screenshots are fabricated. Everything else was verified without a browser; see README.md section 6.

# Produce real evidence

    pip install -r requirements.txt
    python -m playwright install chromium
    cp .env.example .env

    python -m app.server

    python cli.py run-goal --capability lookup_balance --input member_id=12345

    python cli.py replay --artifact artifacts/store/lookup_member_savings_balance_v1.json --input member_id=12345

    python cli.py replay --artifact artifacts/store/lookup_member_savings_balance_v1.json --input member_id=99999

Set ANTHROPIC_API_KEY and CU_CONSOLE_USERNAME/CU_CONSOLE_PASSWORD in .env, or use a local model (README.md section 4) and pass --ollama. Run the server in one terminal and the CLI commands in another.

Then copy artifacts/store/*.json, evidence/logs/*.jsonl, and evidence/screenshots/*.png here if they aren't already in place.

# What to expect

- Discovery log: session_bootstrap_ok, alternating observation/decision records, ending in run_end with success: true.
- Replay log: replay_start, step_ok per step (or known_outcome_matched), then replay_end with the final status. The 99999 run yields business_outcome / member_not_found.
- Screenshots: discovery_..._stepN_*.png per discovery step; replay_..._<step_id>_failed_*.png on replay failure.