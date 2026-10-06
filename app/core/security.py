import uuid
from datetime import UTC, datetime, timedelta

import jwt
from jwt.exceptions import InvalidTokenError
from pwdlib import PasswordHash

from app.core.config import ALGORITHM, settings

password_hash = PasswordHash.recommended()

DUMMY_HASH = password_hash.hash("dummypassword")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return password_hash.verify(plain_password, hashed_password)


def create_token(
    user_id: int,
    token_type: str,
    expires_delta: timedelta,
) -> str:

    expire = datetime.now(UTC) + expires_delta

    payload = {
        "sub": str(user_id),
        "type": token_type,
        "exp": expire,
        "iat": datetime.now(UTC),
        "jti": str(uuid.uuid4()),
    }

    return jwt.encode(
        payload,
        settings.secret_key,
        algorithm=ALGORITHM,
    )


def decode_token(token: str, expected_type: str) -> int:
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[ALGORITHM],
            options={"require": ["exp", "sub", "type", "iat", "jti"]},
        )
        if payload["type"] != expected_type:
            raise InvalidTokenError("wrong token type")
        return int(payload["sub"])
    except ValueError:
        raise InvalidTokenError("bad subject") from None
