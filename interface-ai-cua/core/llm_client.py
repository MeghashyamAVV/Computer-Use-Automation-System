from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Optional

ACTION_TOOL_ANTHROPIC = {
    "name": "agent_action",
    "description": "Choose exactly one next action to take on the current page, or declare the goal complete or that you are stuck.",
    "input_schema": {
        "type": "object",
        "properties": {
            "reasoning": {"type": "string", "description": "One or two sentences: why this action, given the goal and current observation."},
            "action": {
                "type": "string",
                "enum": ["navigate", "click", "type", "select", "extract", "wait_for", "done", "stuck"],
            },
            "target_index": {
                "type": ["integer", "null"],
                "description": "Index of the element from the observation to act on. Required for click/type/select/extract; null otherwise.",
            },
            "value": {
                "type": ["string", "null"],
                "description": "Text to type, URL to navigate to, option to select, or text to wait for. Null for click/done/stuck.",
            },
            "output_key": {
                "type": ["string", "null"],
                "description": "For action=extract: the name of the output field this value should be recorded under.",
            },
            "stuck_reason": {
                "type": ["string", "null"],
                "description": "For action=stuck: a short, specific explanation of what is blocking progress.",
            },
        },
        "required": ["reasoning", "action"],
    },
}

ACTION_TOOL_OPENAI = {
    "type": "function",
    "function": {
        "name": ACTION_TOOL_ANTHROPIC["name"],
        "description": ACTION_TOOL_ANTHROPIC["description"],
        "parameters": ACTION_TOOL_ANTHROPIC["input_schema"],
    },
}


@dataclass
class AgentDecision:
    reasoning: str
    action: str
    target_index: Optional[int] = None
    value: Optional[str] = None
    output_key: Optional[str] = None
    stuck_reason: Optional[str] = None


SYSTEM_PROMPT = """You are an automation agent operating a legacy back-office web application on \
behalf of a bank/credit-union employee. You perceive the page only through a list of \
interactive elements (role, accessible name, current value) -- not raw HTML. You act one \
step at a time by calling the `agent_action` tool.

Rules:
- Only reference elements that appear in the CURRENT observation by their index.
- Prefer the most specific, obviously-correct element for the goal.
- Before clicking a submit/search/continue-style button, check the CURRENT OBSERVATION's \
[value=...] annotations on any text field the goal depends on (e.g. an ID you were asked to \
look up). If a required field is still empty or has the wrong value, fill it with \
action="type" FIRST -- do not click submit on an empty or incorrect form and expect it to work.
- Only use action="extract" on an element that itself DISPLAYS the data value you need (e.g. a \
table cell, a balance figure, a status message) -- never on a button or link, even if its label \
sounds related to the goal. A link like "View Member" is a navigation control, not the data \
itself: click it to navigate to the page where the actual data appears, THEN extract from there.
- After an extract, the history will show you exactly what text you captured. Check it: does it \
actually look like the answer (e.g. a balance should look like a dollar amount, not a UI label \
like "View Member" or "1 result")? If it looks wrong, extract again from a different, more \
specific element -- do not accept an obviously-wrong extraction just to move on.
- CRITICAL: if the goal asks you to read, find, look up, or report any piece of information \
(a balance, a name, an account number, a status, etc.), you MUST call action="extract" on the \
specific element that contains that information, with an output_key naming what it is, BEFORE \
you call action="done". Simply navigating to the screen where the information is visible is \
NOT enough -- the information must be explicitly extracted via its own action="extract" step, \
as a separate step from action="done". output_key belongs ONLY on action="extract" -- setting \
it on action="done" does not count as extracting anything and will be rejected. Only call \
action="done" after the required extract step(s) are complete, and when you do, output_key \
must be null.
- If a page shows an error, a "not found" message, or an access-denied message that is a \
plausible legitimate outcome of the goal, call action="done" (extracting the error/outcome text \
first if there's a natural element to extract it from) -- do not treat this as being stuck.
- Check the ACTION HISTORY before choosing an action: never repeat the exact same action (same \
action type + same target) twice in a row. If your last action should have caused a change and \
the page still looks the same, try something else or reconsider whether it actually worked.
- If you have taken more than a few actions without progress, or the page shows something \
you do not understand and do not know how to proceed safely, call action="stuck" with a \
specific stuck_reason. Do not guess at irreversible actions.
- You MUST respond by calling the agent_action function/tool. Do not respond with plain text.
"""

_VALID_ACTIONS = {"navigate", "click", "type", "select", "extract", "wait_for", "done", "stuck"}


def _decision_from_dict(data: dict) -> AgentDecision:
    if "action" not in data or data["action"] not in _VALID_ACTIONS:
        raise ValueError(f"model returned an invalid or missing 'action': {data.get('action')!r}")

    target_index = data.get("target_index")
    if target_index is not None and not isinstance(target_index, int):
        try:
            target_index = int(str(target_index).strip())
        except (ValueError, TypeError):
            raise ValueError(f"model returned a non-numeric target_index: {target_index!r}")

    value = data.get("value")
    if value is not None and not isinstance(value, str):
        value = str(value)

    return AgentDecision(
        reasoning=data.get("reasoning", ""),
        action=data["action"],
        target_index=target_index,
        value=value,
        output_key=data.get("output_key"),
        stuck_reason=data.get("stuck_reason"),
    )


class LLMClient:
    def __init__(self, mode: str = "live", model: Optional[str] = None,
                 ollama_host: Optional[str] = None, ollama_model: Optional[str] = None):
        self.mode = mode
        self.model = model or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929")

        if mode == "live":
            import anthropic  # imported lazily so other modes never need it configured

            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                raise RuntimeError(
                    "ANTHROPIC_API_KEY is not set. A real discovery run requires either a real "
                    "Anthropic key (mode='live') or a local model via Ollama (mode='ollama'). "
                    "Use --mock only for dry-running the pipeline."
                )
            self._client = anthropic.Anthropic(api_key=api_key)

        elif mode == "ollama":
            import requests  # imported lazily; only needed for this mode

            self._requests = requests
            self.ollama_host = ollama_host or os.environ.get("OLLAMA_HOST", "http://localhost:11434")
            self.ollama_model = ollama_model or os.environ.get("OLLAMA_MODEL", "llama3.1")
            self.ollama_timeout_s = int(os.environ.get("OLLAMA_TIMEOUT_S", "600"))

    def decide(self, goal: str, observation_text: str, history_text: str, screenshot_b64: Optional[str] = None) -> AgentDecision:
        if self.mode == "mock":
            return self._mock_decide(goal, observation_text)
        if self.mode == "ollama":
            return self._ollama_decide(goal, observation_text, history_text)
        return self._anthropic_decide(goal, observation_text, history_text, screenshot_b64)

    def _anthropic_decide(self, goal, observation_text, history_text, screenshot_b64) -> AgentDecision:
        user_content: list[dict[str, Any]] = []
        if screenshot_b64:
            user_content.append(
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": screenshot_b64}}
            )
        user_content.append(
            {
                "type": "text",
                "text": f"GOAL:\n{goal}\n\nACTION HISTORY SO FAR:\n{history_text or '(none yet)'}\n\nCURRENT OBSERVATION:\n{observation_text}",
            }
        )

        resp = self._client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=[ACTION_TOOL_ANTHROPIC],
            tool_choice={"type": "tool", "name": "agent_action"},
            messages=[{"role": "user", "content": user_content}],
        )

        for block in resp.content:
            if block.type == "tool_use" and block.name == "agent_action":
                return _decision_from_dict(block.input)
        raise RuntimeError(f"Model did not return an agent_action tool call: {resp.content}")

    def _ollama_decide(self, goal, observation_text, history_text) -> AgentDecision:
        user_text = (
            f"GOAL:\n{goal}\n\nACTION HISTORY SO FAR:\n{history_text or '(none yet)'}\n\n"
            f"CURRENT OBSERVATION:\n{observation_text}"
        )
        payload = {
            "model": self.ollama_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_text},
            ],
            "tools": [ACTION_TOOL_OPENAI],
            "stream": False,
            "options": {"temperature": 0},
        }

        try:
            resp = self._requests.post(f"{self.ollama_host}/api/chat", json=payload, timeout=self.ollama_timeout_s)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(
                f"Could not reach Ollama at {self.ollama_host}. Is `ollama serve` running? "
                f"(underlying error: {e})"
            )
        if resp.status_code == 404:
            raise RuntimeError(
                f"Model '{self.ollama_model}' not found on Ollama. Run `ollama pull {self.ollama_model}` "
                f"first, or set OLLAMA_MODEL to a model you have (`ollama list`)."
            )
        resp.raise_for_status()
        body = resp.json()
        message = body.get("message", {})

        tool_calls = message.get("tool_calls") or []
        for call in tool_calls:
            fn = call.get("function", {})
            if fn.get("name") == "agent_action":
                args = fn.get("arguments")
                if isinstance(args, str):
                    args = json.loads(args)
                return _decision_from_dict(args)

        content = message.get("content", "")
        parsed = self._extract_json_object(content)
        if parsed is not None:
            return _decision_from_dict(parsed)

        raise RuntimeError(
            f"Ollama model '{self.ollama_model}' did not return a usable agent_action call or JSON "
            f"object. Raw content: {content!r}. Try a model with better tool-calling support, e.g. "
            f"llama3.1, qwen2.5, or mistral-nemo."
        )

    @staticmethod
    def _extract_json_object(text: str) -> Optional[dict]:
        if not text:
            return None
        fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        candidate = fence_match.group(1) if fence_match else text
        if not fence_match:
            brace_match = re.search(r"\{.*\}", text, re.DOTALL)
            if not brace_match:
                return None
            candidate = brace_match.group(0)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _mock_decide(goal: str, observation_text: str) -> AgentDecision:
        """A tiny, deterministic, rule-based stand-in -- NOT an LLM. Only used
        for --mock dry runs of the pipeline. See module docstring."""
        text = observation_text.lower()
        if "member id" in text and "username" not in text:
            if "search" in text and "button" in text:
                return AgentDecision(reasoning="[mock] click Search", action="click", target_index=_find_index(observation_text, "search"))
        return AgentDecision(reasoning="[mock] nothing else to do", action="stuck", stuck_reason="mock LLM has no rule for this observation")


def _find_index(observation_text: str, needle: str) -> int:
    for line in observation_text.splitlines():
        if needle in line.lower():
            try:
                return int(line.strip().split(":")[0])
            except ValueError:
                continue
    return 0
