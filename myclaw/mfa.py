"""MFA / TOTP authentication for ZenSynora Web UI.

Optional dependency: pip install pyotp qrcode

Provides time-based one-time password (TOTP) authentication for the
FastAPI Web UI. When enabled, users must provide a valid TOTP code
to establish a WebSocket connection or access admin endpoints.

Usage:
    from myclaw.mfa import MFAAuth

    mfa = MFAAuth()
    # During setup:
    uri = mfa.provision_user("alice")
    # During verification:
    ok = mfa.verify("alice", "123456")
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import sqlite3
from io import BytesIO
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_MFA_DB_PATH = Path.home() / ".myclaw" / "mfa.db"


try:
    import pyotp

    _PYOTP_AVAILABLE = True
except ImportError:
    _PYOTP_AVAILABLE = False
    pyotp = None  # type: ignore


try:
    import qrcode

    _QR_AVAILABLE = True
except ImportError:
    _QR_AVAILABLE = False


class MFAAuth:
    """TOTP-based multi-factor authentication."""

    def __init__(self, issuer: str = "ZenSynora"):
        self.issuer = issuer
        if not _PYOTP_AVAILABLE:
            logger.warning("pyotp not installed. MFA is disabled. Install: pip install pyotp")

    def _get_db(self) -> sqlite3.Connection:
        _MFA_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(_MFA_DB_PATH))
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE IF NOT EXISTS mfa_users (
                user_id TEXT PRIMARY KEY,
                secret TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )
        """)
        conn.commit()
        return conn

    def is_available(self) -> bool:
        return _PYOTP_AVAILABLE

    def is_enabled_for_user(self, user_id: str) -> bool:
        """Check if MFA is enabled for a user."""
        if not _PYOTP_AVAILABLE:
            return False
        conn = self._get_db()
        row = conn.execute("SELECT enabled FROM mfa_users WHERE user_id = ?", (user_id,)).fetchone()
        conn.close()
        return bool(row and row["enabled"])

    def provision_user(self, user_id: str) -> dict:
        """Provision MFA for a user. Returns provisioning URI and QR code.

        Returns:
            {"provisioning_uri": "...", "qr_code_png_base64": "..."}
        """
        if not _PYOTP_AVAILABLE:
            raise RuntimeError("pyotp is not installed. Run: pip install pyotp")

        secret = pyotp.random_base32()  # type: ignore
        now = __import__("datetime").datetime.utcnow().isoformat()

        conn = self._get_db()
        conn.execute(
            """
            INSERT INTO mfa_users (user_id, secret, enabled, created_at)
            VALUES (?, ?, 1, ?)
            ON CONFLICT(user_id) DO UPDATE SET secret=excluded.secret, enabled=1
            """,
            (user_id, secret, now),
        )
        conn.commit()
        conn.close()

        uri = pyotp.totp.TOTP(secret).provisioning_uri(
            name=user_id,
            issuer_name=self.issuer,
        )

        qr_b64 = ""
        if _QR_AVAILABLE:
            img = qrcode.make(uri)
            buf = BytesIO()
            img.save(buf, format="PNG")
            qr_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

        logger.info(f"MFA provisioned for user: {user_id}")
        # SECURITY FIX: Never return the raw TOTP secret in API responses.
        # The provisioning URI contains the secret and is rendered as a QR code
        # by the client. Returning the raw secret enables account takeover if
        # the response is intercepted or logged.
        return {
            "provisioning_uri": uri,
            "qr_code_png_base64": qr_b64,
        }

    def verify(self, user_id: str, code: str) -> bool:
        """Verify a TOTP code for a user."""
        if not _PYOTP_AVAILABLE:
            return True  # If pyotp not installed, bypass MFA

        conn = self._get_db()
        row = conn.execute(
            "SELECT secret, enabled FROM mfa_users WHERE user_id = ?", (user_id,)
        ).fetchone()
        conn.close()

        if not row or not row["enabled"]:
            return True  # MFA not enabled for this user

        totp = pyotp.TOTP(row["secret"])
        return totp.verify(code, valid_window=1)

    def disable_user(self, user_id: str) -> bool:
        """Disable MFA for a user."""
        if not _PYOTP_AVAILABLE:
            return False

        conn = self._get_db()
        conn.execute("UPDATE mfa_users SET enabled = 0 WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        logger.info(f"MFA disabled for user: {user_id}")
        return True

    def delete_user(self, user_id: str) -> bool:
        """Delete MFA credentials for a user."""
        conn = self._get_db()
        conn.execute("DELETE FROM mfa_users WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        return True
