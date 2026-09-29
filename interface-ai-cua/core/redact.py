import re
from typing import Any

_PATTERNS = [
    (re.compile(r"\b\d{8,}\b"), "[REDACTED_NUMBER]"),
    (re.compile(r"\bSUB-\d+-\d+\b"), "[REDACTED_ACCOUNT]"),
    (re.compile(r"(?i)\b(password|token|secret|api[_-]?key)\s*=\s*[^&\s]+"), r"\1=[REDACTED]"),
    (re.compile(r"(?i)bearer\s+[a-z0-9._-]{10,}"), "Bearer [REDACTED]"),
]

SENTINEL = "[REDACTED]"


def redact_text(text: str) -> str:
    if not isinstance(text, str):
        return text
    out = text
    for pattern, repl in _PATTERNS:
        out = pattern.sub(repl, out)
    return out


def redact_value_for_field(field_name: str, value: Any, sensitive_field_names: set[str]) -> Any:
    if field_name in sensitive_field_names:
        return SENTINEL
    if isinstance(value, str):
        return redact_text(value)
    return value


def redact_dict(d: dict, sensitive_field_names: set[str]) -> dict:
    """Recursively redact a dict, replacing sensitive fields entirely and
    sweeping free-text values through the pattern redactor."""
    out = {}
    for k, v in d.items():
        if k in sensitive_field_names:
            out[k] = SENTINEL
        elif isinstance(v, dict):
            out[k] = redact_dict(v, sensitive_field_names)
        elif isinstance(v, list):
            out[k] = [redact_dict(i, sensitive_field_names) if isinstance(i, dict) else redact_text(i) if isinstance(i, str) else i for i in v]
        elif isinstance(v, str):
            out[k] = redact_text(v)
        else:
            out[k] = v
    return out
