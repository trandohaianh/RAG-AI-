"""Gửi email qua Resend (dùng cho luồng quên mật khẩu)."""
import httpx

from config import RESEND_API_KEY, RESEND_FROM_EMAIL


def is_email_configured() -> bool:
    return bool(RESEND_API_KEY)


async def _send(to_email: str, subject: str, html: str) -> bool:
    if not RESEND_API_KEY:
        return False
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
                json={"from": f"FieldOps <{RESEND_FROM_EMAIL}>", "to": [to_email],
                      "subject": subject, "html": html},
            )
        return resp.status_code < 300
    except Exception:
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
    if not RESEND_API_KEY:
        return False

    html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 480px; margin: 0 auto;">
      <h2 style="color: #4F46E5;">Đặt lại mật khẩu RAG Chat</h2>
      <p>Bạn (hoặc ai đó) vừa yêu cầu đặt lại mật khẩu cho tài khoản <strong>{to_email}</strong>.</p>
      <p>Bấm vào nút bên dưới để đặt mật khẩu mới. Link có hiệu lực trong 30 phút.</p>
      <p style="text-align: center; margin: 32px 0;">
        <a href="{reset_url}"
           style="background: linear-gradient(135deg, #4F46E5, #7C3AED); color: #fff;
                  padding: 12px 28px; border-radius: 10px; text-decoration: none; font-weight: 600;">
          Đặt lại mật khẩu
        </a>
      </p>
      <p style="color: #6B7280; font-size: 13px;">
        Nếu bạn không yêu cầu việc này, có thể bỏ qua email này — mật khẩu hiện tại vẫn an toàn.
      </p>
    </div>
    """

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {RESEND_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": f"RAG Chat <{RESEND_FROM_EMAIL}>",
                    "to": [to_email],
                    "subject": "Đặt lại mật khẩu RAG Chat",
                    "html": html,
                },
            )
        return resp.status_code < 300
    except Exception:
        return False
