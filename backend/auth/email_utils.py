"""Gửi email giao dịch (xác thực tài khoản, quên mật khẩu).

Hai kênh, chọn tự động:
- SMTP (khuyến nghị khi chưa có domain riêng): Gmail + App Password, ~500 email/ngày,
  gửi được tới MỌI địa chỉ mà không cần xác minh DNS. Đặt SMTP_HOST/SMTP_USER/SMTP_PASSWORD.
- Resend API: cần domain đã xác minh để gửi cho người khác (sender test chỉ gửi về chính bạn).
"""

import asyncio
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

import httpx

from config import RESEND_API_KEY, RESEND_FROM_EMAIL

SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "").replace(" ", "")  # App Password Gmail hay có dấu cách
SMTP_FROM = os.getenv("SMTP_FROM", "").strip()
SMTP_USE_SSL = os.getenv("SMTP_USE_SSL", "false").lower() in ("1", "true", "yes")  # cổng 465
EMAIL_SENDER_NAME = os.getenv("EMAIL_SENDER_NAME", "FieldOps")


def _smtp_configured() -> bool:
    return bool(SMTP_HOST and SMTP_USER and SMTP_PASSWORD)


def is_email_configured() -> bool:
    return _smtp_configured() or bool(RESEND_API_KEY)


def email_channel() -> str | None:
    if _smtp_configured():
        return "smtp"
    return "resend" if RESEND_API_KEY else None


def _send_smtp_sync(to_email: str, subject: str, html: str) -> bool:
    msg = EmailMessage()
    msg["From"] = formataddr((EMAIL_SENDER_NAME, SMTP_FROM or SMTP_USER))
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content("Email này cần trình đọc hỗ trợ HTML.")
    msg.add_alternative(html, subtype="html")
    context = ssl.create_default_context()
    if SMTP_USE_SSL:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context, timeout=15) as server:
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
    else:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.starttls(context=context)
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
    return True


async def _send(to_email: str, subject: str, html: str) -> bool:
    """Trả True nếu gửi thành công; không bao giờ raise (lỗi email không làm hỏng đăng ký)."""
    channel = email_channel()
    try:
        if channel == "smtp":
            return await asyncio.to_thread(_send_smtp_sync, to_email, subject, html)
        if channel == "resend":
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    "https://api.resend.com/emails",
                    headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
                    json={
                        "from": f"{EMAIL_SENDER_NAME} <{RESEND_FROM_EMAIL}>",
                        "to": [to_email],
                        "subject": subject,
                        "html": html,
                    },
                )
            return resp.status_code < 300
    except Exception:
        return False
    return False


async def send_verification_email(to_email: str, verify_url: str) -> bool:
    from html import escape

    safe_email = escape(to_email)
    safe_url = escape(verify_url, quote=True)
    html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 480px; margin: 0 auto;">
      <h2 style="color: #4F46E5;">Xác thực email FieldOps</h2>
      <p>Chào bạn, tài khoản <strong>{safe_email}</strong> vừa được tạo trên FieldOps.</p>
      <p>Bấm nút bên dưới để xác thực email. Link có hiệu lực trong 48 giờ.</p>
      <p style="text-align: center; margin: 32px 0;">
        <a href="{safe_url}" style="background: #4F46E5; color: #fff; padding: 12px 28px;
           border-radius: 10px; text-decoration: none; font-weight: 600;">Xác thực email</a>
      </p>
      <p style="color: #6B7280; font-size: 13px;">Nếu bạn không đăng ký, hãy bỏ qua email này.</p>
    </div>
    """
    return await _send(to_email, "Xác thực email FieldOps", html)


async def send_reset_email(to_email: str, reset_url: str) -> bool:
    """Gửi email chứa link đặt lại mật khẩu. Trả về True nếu gửi thành công."""
    from html import escape

    html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 480px; margin: 0 auto;">
      <h2 style="color: #4F46E5;">Đặt lại mật khẩu FieldOps</h2>
      <p>Bạn (hoặc ai đó) vừa yêu cầu đặt lại mật khẩu cho tài khoản <strong>{escape(to_email)}</strong>.</p>
      <p>Bấm vào nút bên dưới để đặt mật khẩu mới. Link có hiệu lực trong 30 phút.</p>
      <p style="text-align: center; margin: 32px 0;">
        <a href="{escape(reset_url, quote=True)}"
           style="background: #4F46E5; color: #fff; padding: 12px 28px; border-radius: 10px;
                  text-decoration: none; font-weight: 600;">Đặt lại mật khẩu</a>
      </p>
      <p style="color: #6B7280; font-size: 13px;">
        Nếu bạn không yêu cầu việc này, có thể bỏ qua email này — mật khẩu hiện tại vẫn an toàn.
      </p>
    </div>
    """
    return await _send(to_email, "Đặt lại mật khẩu FieldOps", html)
