"""
Outbound email, deliberately provider-agnostic.

Every transactional service worth using (Brevo, Resend, SendGrid,
Mailgun, Postmark, or plain Gmail) speaks SMTP, so this uses Python's
standard library rather than a vendor SDK. Switching provider is an
environment-variable change, not a code change, and it adds no new
dependency to deploy.

Two rules this module must never break:

  * Email is best-effort. A booking is a real thing that happened; a
    notification about it is not. Nothing here may raise into a request
    handler, so a patient can never be told their booking failed
    because a mail server was slow.
  * It is silent until configured. With no SMTP settings present it
    logs once and does nothing, so the app runs perfectly well before
    anyone sets this up.
"""

import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

from app.config import (
    SMTP_HOST,
    SMTP_PORT,
    SMTP_USER,
    SMTP_PASSWORD,
    MAIL_FROM,
    MAIL_FROM_NAME,
)

log = logging.getLogger(__name__)

# Don't let a hung mail server tie up a worker.
SMTP_TIMEOUT_SECONDS = 15


def email_configured() -> bool:
    return bool(SMTP_HOST and MAIL_FROM)


def send_email(to: str, subject: str, body_text: str, body_html: str = "") -> bool:
    """
    Returns True if the message was handed to the mail server. Never
    raises - callers treat email as fire-and-forget.
    """
    if not email_configured():
        log.info("Email not configured; skipping message to %s", to)
        return False
    if not to:
        return False

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((MAIL_FROM_NAME or "Doctors Atlas", MAIL_FROM))
    message["To"] = to
    message.set_content(body_text)
    if body_html:
        message.add_alternative(body_html, subtype="html")

    try:
        # Port 465 is implicit TLS; everything else is plain then STARTTLS.
        if int(SMTP_PORT) == 465:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(SMTP_HOST, int(SMTP_PORT), timeout=SMTP_TIMEOUT_SECONDS, context=context) as server:
                if SMTP_USER:
                    server.login(SMTP_USER, SMTP_PASSWORD)
                server.send_message(message)
        else:
            with smtplib.SMTP(SMTP_HOST, int(SMTP_PORT), timeout=SMTP_TIMEOUT_SECONDS) as server:
                server.ehlo()
                try:
                    server.starttls(context=ssl.create_default_context())
                    server.ehlo()
                except smtplib.SMTPNotSupportedError:
                    # Some local/dev relays don't offer STARTTLS.
                    log.warning("SMTP server does not support STARTTLS; sending unencrypted")
                if SMTP_USER:
                    server.login(SMTP_USER, SMTP_PASSWORD)
                server.send_message(message)

        log.info("Sent email to %s: %s", to, subject)
        return True
    except Exception:
        # Logged with a stack trace, but never propagated - the thing
        # the email was about has already happened successfully.
        log.exception("Could not send email to %s", to)
        return False


def send_booking_notification(*, recipients, clinic_name, patient_name, patient_phone,
                              patient_email, when_text, message_text) -> None:
    """
    Tells the clinic a patient has booked. Sent to the doctor and, if
    she's set one, a shared clinic inbox. Each address is sent its own
    copy rather than being CC'd together, so neither recipient sees the
    other's address.
    """
    unique = [r for r in dict.fromkeys(filter(None, recipients))]
    if not unique:
        return

    subject = f"New booking: {patient_name} — {when_text}"

    lines = [
        f"{patient_name} has booked an appointment at {clinic_name}.",
        "",
        f"When:   {when_text}",
        f"Name:   {patient_name}",
        f"Phone:  {patient_phone}",
    ]
    if patient_email:
        lines.append(f"Email:  {patient_email}")
    if message_text:
        lines += ["", "They added a note:", message_text]
    lines += [
        "",
        "This appointment is already in your Appointments page.",
        "",
        "— Doctors Atlas",
    ]
    text = "\n".join(lines)

    def esc(s):
        return (
            str(s or "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )

    note_html = ""
    if message_text:
        note_html = (
            '<p style="margin:18px 0 6px;font-size:13px;color:#6b7a90;">They added a note:</p>'
            f'<p style="margin:0;padding:12px 14px;background:#f4f6fa;border-radius:8px;'
            f'font-size:14px;color:#172033;">{esc(message_text)}</p>'
        )

    row = (
        '<tr><td style="padding:4px 0;font-size:13px;color:#6b7a90;width:70px;">{k}</td>'
        '<td style="padding:4px 0;font-size:14px;color:#172033;font-weight:600;">{v}</td></tr>'
    )
    rows = row.format(k="When", v=esc(when_text))
    rows += row.format(k="Name", v=esc(patient_name))
    rows += row.format(k="Phone", v=esc(patient_phone))
    if patient_email:
        rows += row.format(k="Email", v=esc(patient_email))

    html = f"""<html><body style="margin:0;background:#f4f6fa;padding:24px;
font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;">
  <div style="max-width:520px;margin:0 auto;background:#fff;border-radius:14px;
padding:26px 28px;border:1px solid #e7ebf1;">
    <p style="margin:0 0 4px;font-size:11px;font-weight:700;letter-spacing:.12em;
color:#1a9e8f;text-transform:uppercase;">New booking</p>
    <h1 style="margin:0 0 18px;font-size:20px;color:#15213b;">
      {esc(patient_name)} booked an appointment
    </h1>
    <table style="border-collapse:collapse;">{rows}</table>
    {note_html}
    <p style="margin:22px 0 0;font-size:13px;color:#6b7a90;">
      It's already on your Appointments page.
    </p>
  </div>
  <p style="text-align:center;font-size:11px;color:#98a2b5;margin:16px 0 0;">
    {esc(clinic_name)} &middot; Doctors Atlas
  </p>
</body></html>"""

    for address in unique:
        send_email(address, subject, text, html)
