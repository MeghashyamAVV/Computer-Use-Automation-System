from __future__ import annotations

import queue
import threading
import time
import uuid
from typing import Optional

from flask import Flask, redirect, request, send_from_directory, url_for

CONSOLE_INDEX_HTML = """
<html><head><title>Operator Console</title></head><body>
<h2>Pending intervention requests</h2>
<ul>
{% for rid, info in pending.items() %}
  <li><a href="/console/{{ rid }}">{{ rid }}</a> -- {{ info['status'] }} -- {{ info['reason'] }}</li>
{% endfor %}
</ul>
{% if not pending %}<p>(none)</p>{% endif %}
</body></html>
"""

CONSOLE_DETAIL_HTML = """
<html><head><title>Intervention {{ request_id }}</title></head><body>
<h2>Intervention request {{ request_id }}</h2>
<p><b>Goal:</b> {{ info['goal'] }}</p>
<p><b>Run:</b> {{ info['run_id'] }} &nbsp; <b>Step:</b> {{ info['step'] }}</p>
<p><b>Why it stopped:</b> {{ info['reason'] }}</p>
<p><b>Status:</b> {{ info['status'] }}</p>
{% if info['screenshot'] %}
<p><img src="/shot/{{ screenshot_name }}" style="max-width:700px;border:1px solid #999"></p>
{% endif %}

<h3>Take a manual action on the live session</h3>
<form method="post" action="/console/{{ request_id }}/act">
  <select name="action">
    <option value="click">click</option>
    <option value="type">type</option>
    <option value="navigate">navigate</option>
  </select>
  Role: <input name="role" placeholder="e.g. button, link, textbox">
  Accessible name: <input name="name" placeholder="e.g. Search">
  Value: <input name="value" placeholder="text to type / URL to navigate">
  <button type="submit">Execute on live session</button>
</form>

<form method="post" action="/console/{{ request_id }}/resume">
  <button type="submit">Resume automation</button>
</form>

<p><a href="/console/{{ request_id }}">Refresh</a></p>
</body></html>
"""


class EscalationManager:
    def __init__(self, operator_port: int = 5050, poll_interval: float = 0.4, default_timeout_s: int = 900,
                 screenshot_dir: str = "evidence/screenshots"):
        self.operator_port = operator_port
        self.poll_interval = poll_interval
        self.default_timeout_s = default_timeout_s
        self.screenshot_dir = screenshot_dir
        self.pending: dict[str, dict] = {}
        self.command_queues: dict[str, "queue.Queue"] = {}
        self._app = self._build_app()
        self._thread = threading.Thread(target=self._run_app, daemon=True)
        self._thread.start()
        time.sleep(0.3)  

    def _build_app(self) -> Flask:
        app = Flask("operator_console")
        mgr = self

        @app.route("/")
        def index():
            from flask import render_template_string
            return render_template_string(CONSOLE_INDEX_HTML, pending=mgr.pending)

        @app.route("/console/<request_id>")
        def console(request_id):
            from flask import render_template_string
            info = mgr.pending.get(request_id)
            if not info:
                return "unknown request id", 404
            shot_name = info["screenshot"].split("/")[-1] if info.get("screenshot") else ""
            return render_template_string(CONSOLE_DETAIL_HTML, request_id=request_id, info=info, screenshot_name=shot_name)

        @app.route("/shot/<path:filename>")
        def shot(filename):
            return send_from_directory(mgr.screenshot_dir, filename)

        @app.route("/console/<request_id>/act", methods=["POST"])
        def act(request_id):
            if request_id in mgr.command_queues:
                mgr.command_queues[request_id].put(
                    {
                        "type": "act",
                        "action": request.form.get("action"),
                        "role": request.form.get("role"),
                        "name": request.form.get("name"),
                        "value": request.form.get("value"),
                    }
                )
            return redirect(url_for("console", request_id=request_id))

        @app.route("/console/<request_id>/resume", methods=["POST"])
        def resume(request_id):
            if request_id in mgr.command_queues:
                mgr.command_queues[request_id].put({"type": "resume"})
            return redirect(url_for("index"))

        return app

    def _run_app(self):
        self._app.run(host="127.0.0.1", port=self.operator_port, debug=False, use_reloader=False)

    def request_intervention(self, *, page, logger, goal: str, run_id: str, step, reason: str,
                              screenshot_path: Optional[str]) -> bool:
        request_id = str(uuid.uuid4())[:8]
        self.command_queues[request_id] = queue.Queue()
        self.pending[request_id] = {
            "goal": goal, "run_id": run_id, "step": step, "reason": reason,
            "screenshot": screenshot_path, "status": "waiting",
        }
        logger.log("intervention_requested", request_id=request_id, step=str(step), reason=reason,
                   console_url=f"http://127.0.0.1:{self.operator_port}/console/{request_id}")
        print(
            f"\n>>> HUMAN INTERVENTION REQUESTED ({request_id}) <<<\n"
            f"Reason: {reason}\n"
            f"Open http://127.0.0.1:{self.operator_port}/console/{request_id} to take control of the live session.\n"
        )

        start = time.time()
        while True:
            if time.time() - start > self.default_timeout_s:
                self.pending[request_id]["status"] = "timed_out"
                logger.log("intervention_timeout", request_id=request_id)
                return False
            try:
                cmd = self.command_queues[request_id].get(timeout=self.poll_interval)
            except queue.Empty:
                continue

            if cmd["type"] == "resume":
                self.pending[request_id]["status"] = "resumed"
                logger.log("intervention_resumed", request_id=request_id)
                return True

            if cmd["type"] == "act":
                try:
                    self._execute_manual_command(page, cmd)
                    logger.log("intervention_manual_action", request_id=request_id,
                               action=cmd.get("action"), role=cmd.get("role"), name=cmd.get("name"))
                except Exception as e:  # noqa: BLE001
                    logger.log("intervention_manual_action_error", request_id=request_id, error=str(e))
                new_shot = logger.save_screenshot(page, f"intervention_{request_id}")
                if new_shot:
                    self.pending[request_id]["screenshot"] = new_shot

    @staticmethod
    def _execute_manual_command(page, cmd: dict) -> None:
        action = cmd.get("action")
        if action == "navigate":
            page.goto(cmd["value"])
        elif action == "click":
            page.get_by_role(cmd.get("role") or "button", name=cmd.get("name") or "").first.click()
        elif action == "type":
            page.get_by_role(cmd.get("role") or "textbox", name=cmd.get("name") or "").first.fill(cmd.get("value") or "")
        page.wait_for_load_state("networkidle", timeout=5000)
