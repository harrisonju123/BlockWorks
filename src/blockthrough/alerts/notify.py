"""Notification dispatcher for alert channels (Slack, email).

Uses httpx for async HTTP calls. Email uses stdlib smtplib wrapped in
an executor to stay async-compatible without pulling in aiosmtplib.
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

import httpx

from blockthrough.alerts.types import AlertChannel, AlertEvent, AlertRule, AlertSeverity

logger = logging.getLogger(__name__)

SEVERITY_EMOJI: dict[AlertSeverity, str] = {
    AlertSeverity.INFO: ":information_source:",
    AlertSeverity.WARNING: ":warning:",
    AlertSeverity.CRITICAL: ":rotating_light:",
}


def format_slack_blocks(
    rule: AlertRule,
    event: AlertEvent,
    *,
    current_value: float | None = None,
    threshold_value: float | None = None,
    trend: str | None = None,
) -> list[dict]:
    """Build Slack Block Kit payload for an alert notification."""
    emoji = SEVERITY_EMOJI.get(event.severity, ":grey_question:")
    severity_label = event.severity.value.upper()

    header_text = f"{emoji} *{severity_label}* | {rule.rule_type.value}"
    body_lines = [event.message]

    if current_value is not None:
        body_lines.append(f"*Current:* {current_value:.4f}")
    if threshold_value is not None:
        body_lines.append(f"*Threshold:* {threshold_value:.4f}")
    if trend:
        body_lines.append(f"*Trend:* {trend}")

    body_lines.append(f"*Org:* {rule.org_id}")

    blocks: list[dict] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"{severity_label}: {rule.rule_type.value}"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": header_text},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "\n".join(body_lines)},
        },
    ]

    return blocks


def format_email_body(
    rule: AlertRule,
    event: AlertEvent,
    *,
    current_value: float | None = None,
    threshold_value: float | None = None,
) -> str:
    """Plain-text email body for an alert notification."""
    lines = [
        f"Blockthrough Alert: {event.severity.value.upper()}",
        f"Rule: {rule.rule_type.value}",
        f"Org: {rule.org_id}",
        "",
        event.message,
        "",
    ]
    if current_value is not None:
        lines.append(f"Current value: {current_value:.4f}")
    if threshold_value is not None:
        lines.append(f"Threshold: {threshold_value:.4f}")

    lines.extend(["", "---", "View details in the Blockthrough dashboard."])

    return "\n".join(lines)


async def send_slack_webhook(
    url: str,
    text: str,
    blocks: list[dict] | None = None,
) -> bool:
    """POST a message to a Slack incoming webhook. Returns True on success."""
    payload: dict = {"text": text}
    if blocks:
        payload["blocks"] = blocks

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            return True
    except httpx.HTTPError:
        logger.exception("Failed to send Slack webhook to %s", url)
        return False


def _send_email_sync(
    to: str,
    subject: str,
    body: str,
    smtp_host: str,
    smtp_port: int,
    smtp_from: str,
) -> None:
    """Blocking SMTP send — must run in an executor, never on the event loop."""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = smtp_from
    msg["To"] = to
    msg.set_content(body)

    with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
        server.starttls()
        server.send_message(msg)


async def send_email(
    *,
    to: str,
    subject: str,
    body: str,
    smtp_host: str,
    smtp_port: int = 587,
    smtp_from: str = "alerts@blockthrough.io",
) -> bool:
    """Send a plain-text alert email via SMTP. Returns True on success."""
    import asyncio

    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, _send_email_sync, to, subject, body, smtp_host, smtp_port, smtp_from
        )
        return True
    except Exception:
        logger.exception("Failed to send email to %s via %s:%d", to, smtp_host, smtp_port)
        return False


async def dispatch_alert(
    rule: AlertRule,
    event: AlertEvent,
    *,
    current_value: float | None = None,
    threshold_value: float | None = None,
    trend: str | None = None,
    smtp_host: str | None = None,
    smtp_port: int = 587,
    smtp_from: str = "alerts@blockthrough.io",
) -> None:
    """Send alert to all channels configured on the rule."""
    if rule.channel in (AlertChannel.SLACK, AlertChannel.BOTH):
        webhook_url = rule.webhook_url or rule.threshold_config.get("webhook_url")
        if webhook_url:
            blocks = format_slack_blocks(
                rule,
                event,
                current_value=current_value,
                threshold_value=threshold_value,
                trend=trend,
            )
            await send_slack_webhook(url=webhook_url, text=event.message, blocks=blocks)
        else:
            logger.warning("Slack channel configured but no webhook_url for rule %s", rule.id)

    if rule.channel in (AlertChannel.EMAIL, AlertChannel.BOTH):
        email_to = rule.threshold_config.get("email")
        if email_to and smtp_host:
            body = format_email_body(
                rule,
                event,
                current_value=current_value,
                threshold_value=threshold_value,
            )
            await send_email(
                to=email_to,
                subject=f"[Blockthrough] {rule.rule_type.value} alert - {event.severity.value}",
                body=body,
                smtp_host=smtp_host,
                smtp_port=smtp_port,
                smtp_from=smtp_from,
            )
        else:
            logger.warning(
                "Email channel configured but missing email address or SMTP host for rule %s",
                rule.id,
            )
