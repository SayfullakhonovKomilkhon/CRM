import uuid
from datetime import UTC, datetime, timedelta

import jwt
from pwdlib import PasswordHash

from crm.config import settings

ALGORITHM = "HS256"
password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return password_hash.verify(password, hashed)


def create_access_token(user_id: uuid.UUID) -> str:
    expires_at = datetime.now(UTC) + timedelta(minutes=settings.access_token_expire_minutes)
    return jwt.encode(
        {"sub": str(user_id), "exp": expires_at, "iat": datetime.now(UTC)},
        settings.app_secret_key,
        algorithm=ALGORITHM,
    )


def decode_access_token(token: str) -> uuid.UUID:
    payload = jwt.decode(token, settings.app_secret_key, algorithms=[ALGORITHM])
    return uuid.UUID(payload["sub"])
