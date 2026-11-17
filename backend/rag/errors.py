"""Lỗi nạp tài liệu có thông điệp an toàn để trả nguyên văn cho người dùng."""


class PublicIngestError(ValueError):
    """Thông điệp do chính FieldOps viết (không chứa đường dẫn, SQL hay lỗi của thư viện/provider)."""
