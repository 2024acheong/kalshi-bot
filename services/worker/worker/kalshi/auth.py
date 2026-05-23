from __future__ import annotations

import base64
import os
import time
from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


@dataclass(frozen=True)
class KalshiCredentials:
    api_key_id: str
    private_key_pem: str

    @classmethod
    def from_env(cls) -> "KalshiCredentials | None":
        api_key_id = os.getenv("KALSHI_API_KEY_ID")
        private_key_pem = os.getenv("KALSHI_PRIVATE_KEY")
        private_key_path = os.getenv("KALSHI_PRIVATE_KEY_PATH")
        if not api_key_id:
            return None
        if private_key_pem:
            return cls(api_key_id=api_key_id, private_key_pem=private_key_pem)
        if private_key_path:
            with open(private_key_path, "r", encoding="utf-8") as handle:
                return cls(api_key_id=api_key_id, private_key_pem=handle.read())
        raise RuntimeError("Set KALSHI_PRIVATE_KEY or KALSHI_PRIVATE_KEY_PATH when KALSHI_API_KEY_ID is configured")

    def create_headers(self, *, method: str, path: str) -> dict[str, str]:
        timestamp = str(int(time.time() * 1000))
        signature = self._sign(f"{timestamp}{method}{path.split('?')[0]}")
        return {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-SIGNATURE": signature,
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
        }

    def _sign(self, message: str) -> str:
        private_key = serialization.load_pem_private_key(
            self.private_key_pem.encode("utf-8"),
            password=None,
        )
        signature = private_key.sign(
            message.encode("utf-8"),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        return base64.b64encode(signature).decode("utf-8")
