from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

from core.redact import redact_dict

SENSITIVE_FIELD_NAMES = {"password", "api_key", "authorization", "cookie", "cu_session"}


class RunLogger:
    def __init__(self, run_id: str, log_dir: str = "evidence/logs", screenshot_dir: str = "evidence/screenshots"):
        self.run_id = run_id
        self.log_dir = log_dir
        self.screenshot_dir = screenshot_dir
        os.makedirs(log_dir, exist_ok=True)
        os.makedirs(screenshot_dir, exist_ok=True)
        self.log_path = os.path.join(log_dir, f"{run_id}.jsonl")

    def log(self, event: str, **fields: Any) -> None:
        record = {"ts": time.time(), "run_id": self.run_id, "event": event, **fields}
        record = redact_dict(record, SENSITIVE_FIELD_NAMES)
        with open(self.log_path, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")

    def save_screenshot(self, page, tag: str) -> str:
        filename = f"{self.run_id}_{tag}_{uuid.uuid4().hex[:8]}.png"
        path = os.path.join(self.screenshot_dir, filename)
        try:
            page.screenshot(path=path)
        except Exception as e:
            self.log("screenshot_failed", tag=tag, error=str(e))
            return ""
        return path


def new_run_id(prefix: str) -> str:
    return f"{prefix}_{time.strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:6]}"
