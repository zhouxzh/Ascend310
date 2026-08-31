"""Local-network administration token storage and verification."""

from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path
from typing import Optional, Union

from config import SECRETS_DIR


class AdminAuthError(ValueError):
    pass


class AdminTokenStore:
    """Create one service-owned token and never expose it through the API."""

    def __init__(self, path: Optional[Union[str, Path]] = None):
        default = Path(SECRETS_DIR) / "admin.token"
        self.path = Path(path or os.environ.get("SMART_ALBUM_ADMIN_TOKEN", default))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._token = self._load_or_create()

    def _load_or_create(self) -> str:
        if self.path.is_file():
            token = self.path.read_text(encoding="utf-8").strip()
            if token:
                return token
        token = secrets.token_urlsafe(32)
        fd = os.open(str(self.path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(token + "\n")
        finally:
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        return token

    def verify(self, token: Optional[str]) -> bool:
        return bool(token) and hmac.compare_digest(str(token), self._token)

    def require(self, token: Optional[str]):
        if not self.verify(token):
            raise AdminAuthError("administrator token is required")

    def reveal_for_cli(self) -> str:
        """Explicit local CLI use only; never call this from an HTTP handler."""
        return self._token
