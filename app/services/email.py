"""Brevo transactional email for completed business enquiries."""

from __future__ import annotations

import html
import logging
import time

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

BREVO_SMTP_URL = "https://api.brevo.com/v3/smtp/email"
EMAIL_FAILURE_MESSAGE = (
    "Thank you for your details. We could not notify our team just now. "
    "Please try again in a moment or contact us directly."
)


class EmailSendError(Exception):
    """Raised when the enquiry notification email cannot be sent."""


async def send_enquiry_email(mapped: dict[str, list[dict[str, str]]]) -> None:
    _require_config()
    mapped_data = mapped.get("mapped_data") or []
    subject = _subject_from_mapped(mapped_data)
    payload = {
        "sender": {
            "name": settings.BREVO_SENDER_NAME,
            "email": settings.BREVO_SENDER_EMAIL,
        },
        "to": [{"email": settings.ENQUIRY_EMAIL_TO}],
        "subject": subject,
        "htmlContent": build_enquiry_email_html(mapped_data),
    }
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "api-key": settings.BREVO_API_KEY,
    }
    logger.info("Brevo email send started")
    start_time = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(BREVO_SMTP_URL, json=payload, headers=headers)
        if response.status_code >= 400:
            logger.error("Brevo email send failed | status=%s", response.status_code)
            raise EmailSendError("Enquiry email could not be sent")
        duration_ms = (time.perf_counter() - start_time) * 1000
        logger.info("Brevo email send completed | duration_ms=%.2f", duration_ms)
    except EmailSendError:
        raise
    except Exception:
        logger.exception("Brevo email send failed")
        raise EmailSendError("Enquiry email could not be sent") from None


def build_enquiry_email_html(mapped_data: list[dict[str, str]]) -> str:
    session_id = html.escape(mapped_data[0]["session_id"] if mapped_data else "")
    rows = []
    for item in mapped_data:
        field = html.escape(item.get("field") or "")
        value = html.escape(item.get("value") or "").replace("\n", "<br>")
        rows.append(
            "<tr>"
            f'<td style="border:1px solid #d0d5dd;padding:8px 12px;">{field}</td>'
            f'<td style="border:1px solid #d0d5dd;padding:8px 12px;">{value}</td>'
            "</tr>"
        )
    table_rows = "\n".join(rows)
    return f"""<!DOCTYPE html>
<html>
<body style="font-family:Arial,sans-serif;color:#101828;line-height:1.5;">
  <h2 style="margin-bottom:8px;">New Website Business Enquiry</h2>
  <p><strong>Session ID:</strong> {session_id}</p>
  <table style="border-collapse:collapse;width:100%;max-width:720px;">
    <thead>
      <tr>
        <th style="border:1px solid #d0d5dd;padding:8px 12px;text-align:left;background:#f2f4f7;">Field</th>
        <th style="border:1px solid #d0d5dd;padding:8px 12px;text-align:left;background:#f2f4f7;">Value</th>
      </tr>
    </thead>
    <tbody>
      {table_rows}
    </tbody>
  </table>
</body>
</html>"""


def _subject_from_mapped(mapped_data: list[dict[str, str]]) -> str:
    company_name = ""
    for item in mapped_data:
        if item.get("field") == "Company Name":
            company_name = (item.get("value") or "").strip()
            break
    if company_name:
        return f"New Business Enquiry - {company_name}"
    return "New Website Business Enquiry"


def _require_config() -> None:
    if not settings.BREVO_API_KEY.strip():
        logger.error("Enquiry email is not configured: missing BREVO_API_KEY")
        raise EmailSendError("Enquiry email is not configured")
    if not settings.ENQUIRY_EMAIL_TO.strip():
        logger.error("Enquiry email is not configured: missing ENQUIRY_EMAIL_TO")
        raise EmailSendError("Enquiry email is not configured")
    if not settings.BREVO_SENDER_EMAIL.strip():
        logger.error("Enquiry email is not configured: missing BREVO_SENDER_EMAIL")
        raise EmailSendError("Enquiry email is not configured")
