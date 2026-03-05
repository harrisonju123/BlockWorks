"""Tests for notification formatting and dispatch.

All HTTP calls are mocked -- no real webhooks or SMTP connections.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from blockthrough.alerts.notify import (
    SEVERITY_EMOJI,
    dispatch_alert,
    format_email_body,
    format_slack_blocks,
    send_slack_webhook,
)
from blockthrough.alerts.types import (
    AlertChannel,
    AlertEvent,
    AlertRule,
    AlertSeverity,
    RuleType,
)


def _rule(
    channel: AlertChannel = AlertChannel.SLACK,
    webhook_url: str = "https://hooks.slack.com/test",
    rule_type: RuleType = RuleType.SPEND_THRESHOLD,
) -> AlertRule:
    return AlertRule(
        id=uuid.uuid4(),
        org_id="org-test",
        rule_type=rule_type,
        threshold_config={"amount_usd": 500.0, "period": "daily", "webhook_url": webhook_url},
        channel=channel,
        webhook_url=webhook_url,
    )


def _event(severity: AlertSeverity = AlertSeverity.WARNING) -> AlertEvent:
    return AlertEvent(
        rule_id=uuid.uuid4(),
        triggered_at=datetime.now(timezone.utc),
        message="Daily spend exceeded $500.00",
        severity=severity,
    )


class TestSlackBlockFormatting:

    def test_blocks_contain_header(self) -> None:
        blocks = format_slack_blocks(_rule(), _event())
        assert any(b["type"] == "header" for b in blocks)

    def test_header_contains_severity(self) -> None:
        blocks = format_slack_blocks(_rule(), _event(AlertSeverity.CRITICAL))
        header = next(b for b in blocks if b["type"] == "header")
        assert "CRITICAL" in header["text"]["text"]

    def test_blocks_contain_message(self) -> None:
        event = _event()
        blocks = format_slack_blocks(_rule(), event)
        section_texts = [b["text"]["text"] for b in blocks if b["type"] == "section"]
        body = "\n".join(section_texts)
        assert event.message in body

    def test_blocks_contain_org_id(self) -> None:
        rule = _rule()
        blocks = format_slack_blocks(rule, _event())
        section_texts = [b["text"]["text"] for b in blocks if b["type"] == "section"]
        body = "\n".join(section_texts)
        assert rule.org_id in body

    def test_current_and_threshold_values_included(self) -> None:
        blocks = format_slack_blocks(
            _rule(), _event(),
            current_value=523.45,
            threshold_value=500.0,
        )
        section_texts = [b["text"]["text"] for b in blocks if b["type"] == "section"]
        body = "\n".join(section_texts)
        assert "523.4500" in body
        assert "500.0000" in body

    def test_trend_direction_included(self) -> None:
        blocks = format_slack_blocks(
            _rule(), _event(),
            trend="increasing",
        )
        section_texts = [b["text"]["text"] for b in blocks if b["type"] == "section"]
        body = "\n".join(section_texts)
        assert "increasing" in body

    def test_severity_emoji_mapping(self) -> None:
        for severity, emoji in SEVERITY_EMOJI.items():
            blocks = format_slack_blocks(_rule(), _event(severity))
            section_texts = [b["text"]["text"] for b in blocks if b["type"] == "section"]
            body = "\n".join(section_texts)
            assert emoji in body

    def test_all_rule_types(self) -> None:
        for rt in RuleType:
            blocks = format_slack_blocks(
                _rule(rule_type=rt), _event()
            )
            header = next(b for b in blocks if b["type"] == "header")
            assert rt.value in header["text"]["text"]


class TestEmailBodyFormatting:

    def test_body_contains_severity(self) -> None:
        body = format_email_body(_rule(), _event(AlertSeverity.CRITICAL))
        assert "CRITICAL" in body

    def test_body_contains_message(self) -> None:
        event = _event()
        body = format_email_body(_rule(), event)
        assert event.message in body

    def test_body_contains_org_id(self) -> None:
        rule = _rule()
        body = format_email_body(rule, _event())
        assert rule.org_id in body

    def test_body_contains_rule_type(self) -> None:
        rule = _rule(rule_type=RuleType.ANOMALY_ZSCORE)
        body = format_email_body(rule, _event())
        assert "anomaly_zscore" in body

    def test_body_contains_values_when_provided(self) -> None:
        body = format_email_body(
            _rule(), _event(),
            current_value=123.456,
            threshold_value=100.0,
        )
        assert "123.4560" in body
        assert "100.0000" in body

    def test_body_contains_dashboard_link(self) -> None:
        body = format_email_body(_rule(), _event())
        assert "dashboard" in body.lower()


class TestSendSlackWebhook:

    @pytest.mark.asyncio
    async def test_successful_post(self) -> None:
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        with patch("blockthrough.alerts.notify.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await send_slack_webhook(
                "https://hooks.slack.com/test",
                "test message",
                blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": "hello"}}],
            )

            assert result is True
            mock_client.post.assert_called_once()
            call_kwargs = mock_client.post.call_args
            assert call_kwargs[0][0] == "https://hooks.slack.com/test"
            payload = call_kwargs[1]["json"]
            assert payload["text"] == "test message"
            assert "blocks" in payload

    @pytest.mark.asyncio
    async def test_http_error_returns_false(self) -> None:
        import httpx as _httpx

        with patch("blockthrough.alerts.notify.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(
                side_effect=_httpx.HTTPStatusError(
                    "404", request=MagicMock(), response=MagicMock()
                )
            )
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await send_slack_webhook("https://hooks.slack.com/bad", "msg")
            assert result is False


class TestDispatchAlert:

    @pytest.mark.asyncio
    async def test_slack_channel_sends_webhook(self) -> None:
        rule = _rule(channel=AlertChannel.SLACK)
        event = _event()

        with patch("blockthrough.alerts.notify.send_slack_webhook", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = True
            await dispatch_alert(rule, event)
            mock_send.assert_called_once()

    @pytest.mark.asyncio
    async def test_email_channel_sends_email(self) -> None:
        rule = AlertRule(
            id=uuid.uuid4(),
            org_id="org-test",
            rule_type=RuleType.SPEND_THRESHOLD,
            threshold_config={"email": "admin@test.com"},
            channel=AlertChannel.EMAIL,
        )
        event = _event()

        with patch("blockthrough.alerts.notify.send_email", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = True
            await dispatch_alert(rule, event, smtp_host="smtp.test.com")
            mock_send.assert_called_once()

    @pytest.mark.asyncio
    async def test_both_channel_sends_slack_and_email(self) -> None:
        rule = AlertRule(
            id=uuid.uuid4(),
            org_id="org-test",
            rule_type=RuleType.SPEND_THRESHOLD,
            threshold_config={
                "webhook_url": "https://hooks.slack.com/test",
                "email": "admin@test.com",
            },
            channel=AlertChannel.BOTH,
            webhook_url="https://hooks.slack.com/test",
        )
        event = _event()

        with (
            patch("blockthrough.alerts.notify.send_slack_webhook", new_callable=AsyncMock) as mock_slack,
            patch("blockthrough.alerts.notify.send_email", new_callable=AsyncMock) as mock_email,
        ):
            mock_slack.return_value = True
            mock_email.return_value = True
            await dispatch_alert(rule, event, smtp_host="smtp.test.com")
            mock_slack.assert_called_once()
            mock_email.assert_called_once()
