"""Tạo/lấy embed token dev cho user đầu tiên. In token ra stdout."""
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from config import DATA_ROOT
from database import SessionLocal, init_db
from auth.models import User
from saas.models import EmbedToken
from saas.security import generate_embed_token, hash_secret

TOKEN_FILE = DATA_ROOT / "dev-embed-token.txt"
LABEL = "Dev Micro"


def main() -> None:
    init_db()
    if TOKEN_FILE.exists():
        t = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if t:
            db = SessionLocal()
            try:
                h = hash_secret(t)
                ok = (
                    db.query(EmbedToken)
                    .filter(EmbedToken.token_hash == h, EmbedToken.active.is_(True))
                    .first()
                )
                if ok:
                    print(t)
                    return
            finally:
                db.close()

    db = SessionLocal()
    try:
        user = db.query(User).order_by(User.id).first()
        if not user:
            sys.stderr.write("Chua co user. Dang ky tai /register truoc.\n")
            sys.exit(1)
        raw, h = generate_embed_token()
        row = EmbedToken(
            user_id=user.id,
            label=LABEL,
            token_hash=h,
            allowed_origins=None,
            active=True,
        )
        db.add(row)
        db.commit()
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(raw, encoding="utf-8")
        print(raw)
    finally:
        db.close()


if __name__ == "__main__":
    main()
