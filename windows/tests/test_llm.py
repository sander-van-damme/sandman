"""Tests for the LLM client — no real network calls."""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import MagicMock

from windows.llm import ConversationHistory, LLMClient, NudgeDecision


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
