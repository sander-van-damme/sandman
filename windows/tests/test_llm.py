"""Tests for the LLM client — no real network calls."""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import MagicMock

from windows.llm import MAX_COMPLETION_TOKENS, ConversationHistory, LLMClient, NudgeDecision


def test_conversation_history_trims() -> None:
    h = ConversationHistory(max_messages=3)
    for i in range(5):
        h.add("user", f"msg {i}")
    assert len(h.messages) == 3
    assert h.messages[0]["content"] == "msg 2"
    assert h.messages[-1]["content"] == "msg 4"


def test_conversation_history_clear() -> None:
    h = ConversationHistory()
    h.start_session()
    h.add("user", "hi")
    h.clear()
    assert h.messages == []
    assert h.session_started_at is None


def test_parse_decision_valid_json() -> None:
    content = json.dumps(
        {
            "activity_type": "programming",
            "should_nudge": True,
            "reason": "late coding",
            "message": "Close the laptop.",
            "follow_up_question": "What are you trying to finish?",
        }
    )
    decision = LLMClient._parse_decision(content, nudge_count=0)
    assert decision.should_nudge is True
    assert decision.activity_type == "programming"
    assert decision.message == "Close the laptop."
    assert decision.follow_up_question == "What are you trying to finish?"


def test_parse_decision_invalid_json_falls_back() -> None:
    decision = LLMClient._parse_decision("not json at all", nudge_count=2)
    assert decision.should_nudge is True  # fallback always nudges
    assert decision.reason == "invalid_json"
    assert decision.message


def test_parse_decision_empty_json_object_falls_back() -> None:
    decision = LLMClient._parse_decision("{}", nudge_count=1)
    assert decision.should_nudge is True
    assert decision.reason == "empty_json"
    assert decision.message


def test_build_system_prompt_fills_template() -> None:
    prompt = LLMClient.build_system_prompt(
        now=datetime(2026, 4, 15, 23, 30),
        bedtime="21:30",
        wake_time="07:30",
        minutes_past=120,
        app_name="Code.exe",
        window_title="main.py",
        nudge_count=3,
        nudge_style="direct",
    )
    assert "23:30" in prompt
    assert "Code.exe" in prompt
    assert "main.py" in prompt
    assert "direct" in prompt
    assert "Number of nudges sent this session: 3" in prompt


def test_classify_and_nudge_calls_openai() -> None:
    client = LLMClient(api_key="sk-test")

    fake_openai = MagicMock()
    fake_openai.chat.completions.create.return_value = MagicMock(
        choices=[
            MagicMock(
                message=MagicMock(
                    content=json.dumps(
                        {
                            "activity_type": "social_media",
                            "should_nudge": True,
                            "reason": "scrolling",
                            "message": "Put the phone down.",
                        }
                    )
                )
            )
        ]
    )
    client._client = fake_openai  # bypass real SDK init

    history = ConversationHistory()
    decision = client.classify_and_nudge(
        system_prompt="sys", history=history, nudge_count=1
    )
    assert decision.should_nudge
    assert decision.message == "Put the phone down."
    fake_openai.chat.completions.create.assert_called_once()
    kwargs = fake_openai.chat.completions.create.call_args.kwargs
    assert kwargs["max_completion_tokens"] == MAX_COMPLETION_TOKENS


def test_classify_and_nudge_fallback_on_exception() -> None:
    client = LLMClient(api_key="sk-test")
    fake = MagicMock()
    fake.chat.completions.create.side_effect = RuntimeError("network down")
    client._client = fake

    decision = client.classify_and_nudge(
        system_prompt="sys", history=ConversationHistory(), nudge_count=0
    )
    assert isinstance(decision, NudgeDecision)
    assert decision.should_nudge is True
    assert decision.reason.startswith("api_error")


def test_classify_and_nudge_fallback_on_empty_content() -> None:
    """When the LLM returns empty message.content, use fallback nudge."""
    client = LLMClient(api_key="sk-test")

    fake_openai = MagicMock()
    fake_openai.chat.completions.create.return_value = MagicMock(
        choices=[
            MagicMock(
                finish_reason="stop",
                message=MagicMock(content="", refusal=None),
            )
        ]
    )
    client._client = fake_openai

    decision = client.classify_and_nudge(
        system_prompt="sys", history=ConversationHistory(), nudge_count=1
    )
    assert decision.should_nudge is True
    assert decision.reason == "empty_response"
    assert decision.message  # fallback message is non-empty


def test_classify_and_nudge_fallback_on_none_content() -> None:
    """When message.content is None, use fallback nudge."""
    client = LLMClient(api_key="sk-test")

    fake_openai = MagicMock()
    fake_openai.chat.completions.create.return_value = MagicMock(
        choices=[
            MagicMock(
                finish_reason="stop",
                message=MagicMock(content=None, refusal=None),
            )
        ]
    )
    client._client = fake_openai

    decision = client.classify_and_nudge(
        system_prompt="sys", history=ConversationHistory(), nudge_count=0
    )
    assert decision.should_nudge is True
    assert decision.reason == "empty_response"
    assert decision.message


def test_extract_response_content_with_content_parts() -> None:
    response = MagicMock(
        choices=[
            MagicMock(
                message=MagicMock(
                    content=[
                        {"type": "text", "text": '{"activity_type":"utility",'},
                        {"type": "text", "text": '"should_nudge":true,"reason":"late"'},
                        {"type": "text", "text": ',"message":"Wrap up now."}'},
                    ]
                )
            )
        ]
    )
    content = LLMClient._extract_response_content(response)
    decision = LLMClient._parse_decision(content, nudge_count=0)
    assert decision.should_nudge is True
    assert decision.message == "Wrap up now."


def test_extract_response_content_uses_message_parsed_dict() -> None:
    response = MagicMock(
        choices=[
            MagicMock(
                message=MagicMock(
                    content="",
                    parsed={
                        "activity_type": "productive",
                        "should_nudge": True,
                        "reason": "late night",
                        "message": "Save your work and wind down.",
                    },
                    refusal=None,
                )
            )
        ]
    )
    content = LLMClient._extract_response_content(response)
    assert isinstance(content, dict)
    decision = LLMClient._parse_decision(content, nudge_count=0)
    assert decision.should_nudge is True
    assert decision.message == "Save your work and wind down."


def test_serialize_response_uses_model_dump_json() -> None:
    response = MagicMock()
    response.model_dump_json.return_value = '{"ok": true}'

    serialized = LLMClient._serialize_response(response)

    assert serialized == '{"ok": true}'


def test_serialize_response_falls_back_to_repr() -> None:
    class BareResponse:
        def __repr__(self) -> str:
            return "<bare-response>"

    serialized = LLMClient._serialize_response(BareResponse())

    assert serialized == "<bare-response>"
