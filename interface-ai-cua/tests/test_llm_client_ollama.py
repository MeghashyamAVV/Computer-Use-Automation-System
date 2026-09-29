from unittest.mock import MagicMock, patch

from core.llm_client import LLMClient, _decision_from_dict


def _make_ollama_client():
    return LLMClient(mode="ollama", ollama_host="http://fake-host:11434", ollama_model="fake-model")


def _fake_response(status_code=200, json_body=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body or {}
    resp.raise_for_status = MagicMock()
    return resp


def test_ollama_decide_uses_proper_tool_call_when_present():
    client = _make_ollama_client()
    body = {
        "message": {
            "tool_calls": [
                {
                    "function": {
                        "name": "agent_action",
                        "arguments": {"reasoning": "click search", "action": "click", "target_index": 3},
                    }
                }
            ]
        }
    }
    with patch.object(client._requests, "post", return_value=_fake_response(200, body)):
        decision = client.decide("goal", "obs", "")
    assert decision.action == "click"
    assert decision.target_index == 3


def test_ollama_decide_parses_string_arguments():
    client = _make_ollama_client()
    body = {
        "message": {
            "tool_calls": [
                {"function": {"name": "agent_action", "arguments": '{"reasoning": "r", "action": "done"}'}}
            ]
        }
    }
    with patch.object(client._requests, "post", return_value=_fake_response(200, body)):
        decision = client.decide("goal", "obs", "")
    assert decision.action == "done"


def test_ollama_decide_falls_back_to_json_in_content():
    client = _make_ollama_client()
    body = {
        "message": {
            "content": 'Sure, here is my action:\n```json\n{"reasoning": "typing id", "action": "type", "target_index": 1, "value": "12345"}\n```'
        }
    }
    with patch.object(client._requests, "post", return_value=_fake_response(200, body)):
        decision = client.decide("goal", "obs", "")
    assert decision.action == "type"
    assert decision.value == "12345"


def test_ollama_decide_raises_clear_error_when_nothing_usable():
    client = _make_ollama_client()
    body = {"message": {"content": "I am not sure what to do here."}}
    with patch.object(client._requests, "post", return_value=_fake_response(200, body)):
        try:
            client.decide("goal", "obs", "")
            assert False, "expected RuntimeError"
        except RuntimeError as e:
            assert "did not return a usable" in str(e)


def test_ollama_decide_raises_helpful_error_on_missing_model():
    client = _make_ollama_client()
    with patch.object(client._requests, "post", return_value=_fake_response(404, {})):
        try:
            client.decide("goal", "obs", "")
            assert False, "expected RuntimeError"
        except RuntimeError as e:
            assert "ollama pull" in str(e)


def test_decision_from_dict_rejects_invalid_action():
    try:
        _decision_from_dict({"reasoning": "r", "action": "delete_everything"})
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_decision_from_dict_coerces_string_target_index_to_int():
    decision = _decision_from_dict({"reasoning": "r", "action": "click", "target_index": "3"})
    assert decision.target_index == 3
    assert isinstance(decision.target_index, int)


def test_decision_from_dict_leaves_integer_target_index_alone():
    decision = _decision_from_dict({"reasoning": "r", "action": "click", "target_index": 3})
    assert decision.target_index == 3


def test_decision_from_dict_coerces_non_string_value_to_string():
    decision = _decision_from_dict({"reasoning": "r", "action": "type", "target_index": 1, "value": 12345})
    assert decision.value == "12345"
    assert isinstance(decision.value, str)


def test_decision_from_dict_rejects_non_numeric_target_index():
    try:
        _decision_from_dict({"reasoning": "r", "action": "click", "target_index": "not-a-number"})
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_ollama_decide_handles_string_target_index_from_model():
    client = _make_ollama_client()
    body = {
        "message": {
            "tool_calls": [
                {"function": {"name": "agent_action", "arguments": {"reasoning": "r", "action": "click", "target_index": "2"}}}
            ]
        }
    }
    with patch.object(client._requests, "post", return_value=_fake_response(200, body)):
        decision = client.decide("goal", "obs", "")
    assert decision.target_index == 2
    assert isinstance(decision.target_index, int)
