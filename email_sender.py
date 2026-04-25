import smtplib
from email.message import EmailMessage


def send_invoice_email(
    smtp_user: str,
    smtp_password: str,
    to_addrs: list[str],
    cc_addrs: list[str],
    subject: str,
    body: str,
    xlsx_bytes: bytes,
    filename: str,
) -> None:
    """Send an invoice .xlsx attachment via Google Workspace SMTP.

    Uses smtp.gmail.com:587 with STARTTLS. smtp_password must be a
    Google App Password (not the regular account password).
    """
    msg = EmailMessage()
    msg["From"] = smtp_user
    msg["To"] = ", ".join(to_addrs)
    if cc_addrs:
        msg["Cc"] = ", ".join(cc_addrs)
    msg["Subject"] = subject
    msg.set_content(body)

    msg.add_attachment(
        xlsx_bytes,
        maintype="application",
        subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=filename,
    )

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(smtp_user, smtp_password)
        server.send_message(msg, to_addrs=to_addrs + cc_addrs)
