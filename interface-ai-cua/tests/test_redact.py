from core.redact import redact_dict, redact_text


def test_redacts_long_digit_runs():
    assert "[REDACTED_NUMBER]" in redact_text("member account 123456789 was updated")


def test_redacts_generated_account_numbers():
    assert redact_text("opened SUB-12345-4821 successfully") == "opened [REDACTED_ACCOUNT] successfully"


def test_redacts_password_query_param():
    out = redact_text("POST /login?username=op&password=hunter2")
    assert "hunter2" not in out
    assert "password=[REDACTED]" in out


def test_redact_dict_removes_sensitive_fields_entirely():
    d = {"event": "login", "password": "hunter2", "nested": {"cu_session": "abcd-1234", "ok": True}}
    out = redact_dict(d, {"password", "cu_session"})
    assert out["password"] == "[REDACTED]"
    assert out["nested"]["cu_session"] == "[REDACTED]"
    assert out["nested"]["ok"] is True


def test_redact_dict_sweeps_free_text_values():
    d = {"log_line": "created account SUB-999-1111 for member 999999999"}
    out = redact_dict(d, set())
    assert "SUB-999-1111" not in out["log_line"]
    assert "999999999" not in out["log_line"]
