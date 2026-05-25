"""Dependencies FastAPI — xác thực JWT."""

from fastapi import Depends, HTTPException, status

from fastapi.security import OAuth2PasswordBearer

from sqlalchemy.orm import Session



from auth.models import User

from auth.schemas import CurrentUser

from auth.service import decode_token

from database import get_db



oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")





async def get_current_user(

    token: str = Depends(oauth2_scheme),

    db: Session = Depends(get_db),

) -> CurrentUser:

    """Lấy user hiện tại từ Bearer token (DTO, không phụ thuộc session)."""

    payload = decode_token(token)

    if not payload or "sub" not in payload:

        raise HTTPException(

            status_code=status.HTTP_401_UNAUTHORIZED,

            detail="Token không hợp lệ",

            headers={"WWW-Authenticate": "Bearer"},

        )

    row = db.query(User).filter(User.email == payload["sub"]).first()

    if not row:

        raise HTTPException(status_code=401, detail="Người dùng không tồn tại")

    return CurrentUser(
        id=row.id,
        email=row.email,
        role=row.role,
        subscription_plan=getattr(row, "subscription_plan", None) or "free",
    )





async def require_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:

    """Chỉ cho phép role admin."""

    if user.role != "admin":

        raise HTTPException(status_code=403, detail="Cần quyền admin")

    return user

