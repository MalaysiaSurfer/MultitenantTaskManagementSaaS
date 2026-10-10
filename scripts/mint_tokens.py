import uuid
from datetime import UTC, datetime, timedelta

import jwt
from app.core.config import ALGORITHM, settings


def mint(sub: str, minutes: int, token_type: str = "access") -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": sub,
            "type": token_type,
            "iat": now,
            "exp": now + timedelta(minutes=minutes),
            "jti": str(uuid.uuid4()),
        },
        settings.secret_key,
        algorithm=ALGORITHM,
    )


if __name__ == "__main__":
    print("BAD_SUB=" + mint("abc", 15))
    print("EXPIRED=" + mint("1", -1))
    print("VALID=" + mint("1", 15))
