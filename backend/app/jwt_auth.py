from __future__ import annotations

import base64
from typing import Any

import jwt


JWT_ALGORITHM = "HS256"


def _base64_encoded_secret(secret: str) -> str:
    return base64.b64encode(secret.encode("utf-8")).decode("utf-8")


def get_java_compatible_signing_key(secret: str) -> bytes:
    # 中文注释：Java 侧先把原始秘钥做 Base64，再在签名/验签时使用解码后的字节。
    encoded_secret = _base64_encoded_secret(secret)
    return base64.b64decode(encoded_secret)


def decode_java_compatible_jwt(token: str, secret: str) -> dict[str, Any]:
    return jwt.decode(
        token,
        get_java_compatible_signing_key(secret),
        algorithms=[JWT_ALGORITHM],
    )
