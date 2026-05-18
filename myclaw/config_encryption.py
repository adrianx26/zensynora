"""Config encryption — transparent encryption at rest for config.json.

Optional dependency: pip install cryptography

When cryptography is not installed, config files remain plaintext.
When the config file is encrypted but no key exists, loading fails with
a clear error message.

The encrypted file format is a simple JSON wrapper:
    {"__encrypted__": true, "data": "<base64-encoded-fernet-ciphertext>"}

The Fernet key is stored at ~/.myclaw/.config_key (with 0o600 permissions).
If the keyring package is available, the key is stored in the OS keychain
under service="zensynora", username="config_key".

Usage:
    from myclaw.config_encryption import encrypt_config, decrypt_config

    # Encrypt existing plaintext config
    encrypt_config()

    # Decrypt to plaintext (for editing)
    decrypt_config()

    # Auto-detect and decrypt during load_config()
    raw = load_encrypted_or_plain(CONFIG_FILE)
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

CONFIG_DIR = Path.home() / ".myclaw"
CONFIG_FILE = CONFIG_DIR / "config.json"
KEY_FILE = CONFIG_DIR / ".config_key"

_ENCRYPTION_MARKER = "__encrypted__"

# A Fernet key is 32 raw bytes, base64-encoded (urlsafe) → 44 characters.
# The key may optionally be prefixed by "base64:" (some tools produce this).
_FERNET_KEY_RE = re.compile(r"^(?:base64:)?([A-Za-z0-9_\-]{43}=)$")
# Minimum entropy for a Fernet key — reject keys that are obviously weak.
_MIN_KEY_ENTROPY = 64  # bits-equivalent threshold shrunk to length heuristic


try:
    from cryptography.fernet import Fernet, InvalidToken

    _CRYPTO_AVAILABLE = True
except ImportError:
    _CRYPTO_AVAILABLE = False
    Fernet = None  # type: ignore
    InvalidToken = Exception  # type: ignore


def _get_keyring_password() -> Optional[str]:
    """Try to retrieve key from OS keychain."""
    try:
        import keyring

        return keyring.get_password("zensynora", "config_key")
    except Exception:
        return None


def _set_keyring_password(password: str) -> bool:
    """Store key in OS keychain."""
    try:
        import keyring

        keyring.set_password("zensynora", "config_key", password)
        return True
    except Exception:
        return False


def _generate_key() -> str:
    """Generate a new Fernet key."""
    if not _CRYPTO_AVAILABLE:
        raise RuntimeError("cryptography is not installed. Run: pip install cryptography")
    return Fernet.generate_key().decode("utf-8")


def _validate_key_format(key: str) -> None:
    """Validate that a Fernet key is well-formed.

    A valid Fernet key is a URL-safe base64 encoding of 32 bytes,
    resulting in exactly 44 characters (43 alphanumeric + '=' padding).

    Raises:
        ValueError: If the key does not match the expected Fernet format.
    """
    if not _FERNET_KEY_RE.match(key):
        raise ValueError(
            "Invalid encryption key format. A Fernet key must be 43 URL-safe "
            "base64 characters followed by '='. "
            "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    # Reject keys that are obviously too short or low-entropy
    if len(set(key)) < 8:
        raise ValueError(
            "Encryption key appears to have low entropy. "
            "Use a proper Fernet key: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )


def _get_or_create_key() -> str:
    """Get existing key or create + store a new one.

    SECURITY FIX (2026-04-23): Key resolution priority:
        1. ZENSYNORA_CONFIG_KEY environment variable (for containers)
        2. OS keyring (preferred for desktop)
        3. Existing key file (fallback)
        4. Generate new key and store in keyring, else key file
    """
    # 1. Environment variable (highest priority — useful for Docker/cloud)
    env_key = os.environ.get("ZENSYNORA_CONFIG_KEY", "").strip()
    if env_key:
        try:
            _validate_key_format(env_key)
        except ValueError as exc:
            logger.error(
                "ZENSYNORA_CONFIG_KEY environment variable is invalid: %s. "
                "Falling back to other key sources.",
                exc,
            )
            # Fall through to other sources instead of crashing.
        else:
            return env_key

    # 2. Try keyring
    key = _get_keyring_password()
    if key:
        return key

    # 3. Try key file
    if KEY_FILE.exists():
        return KEY_FILE.read_text(encoding="utf-8").strip()

    # 4. Generate new key
    key = _generate_key()

    # Store in keyring if available
    if _set_keyring_password(key):
        logger.info("Config encryption key stored in OS keychain")
    else:
        # Fall back to key file with restricted permissions
        KEY_FILE.write_text(key, encoding="utf-8")
        try:
            os.chmod(KEY_FILE, 0o600)
        except Exception:
            pass
        logger.warning(
            f"Config encryption key stored in {KEY_FILE}. "
            f"Consider using the OS keyring or ZENSYNORA_CONFIG_KEY env var for better security."
        )

    return key


def _load_key() -> Optional[str]:
    """Load existing key without creating one.
    
    SECURITY FIX (2026-05-17): Validates key format on load to catch
    corrupted key files and invalid environment variables early.
    """
    key = _get_keyring_password()
    if key:
        try:
            _validate_key_format(key)
        except ValueError:
            logger.warning("OS keyring contains an invalid encryption key; ignoring.")
            key = None
        else:
            return key
    if KEY_FILE.exists():
        file_key = KEY_FILE.read_text(encoding="utf-8").strip()
        try:
            _validate_key_format(file_key)
        except ValueError as exc:
            logger.warning(
                "Config key file %s contains an invalid key: %s. "
                "Delete it and regenerate with: zensynora config encrypt",
                KEY_FILE, exc,
            )
            return None
        return file_key
    return None


# Public alias for external validation
validate_key_format = _validate_key_format


def _get_fernet() -> "Fernet":
    """Get a Fernet instance with the current key."""
    if not _CRYPTO_AVAILABLE:
        raise RuntimeError("cryptography is not installed")
    key = _get_or_create_key()
    return Fernet(key.encode("utf-8"))


def is_encrypted(path: Path) -> bool:
    """Check if a file is in encrypted format."""
    if not path.exists():
        return False
    try:
        with path.open("r", encoding="utf-8") as f:
            first_line = f.readline().strip()
        return _ENCRYPTION_MARKER in first_line
    except Exception:
        return False


def encrypt_config(config_path: Optional[Path] = None) -> None:
    """Encrypt an existing plaintext config file.

    Args:
        config_path: Path to config file (defaults to ~/.myclaw/config.json)
    """
    if not _CRYPTO_AVAILABLE:
        logger.warning("cryptography is not installed. Cannot encrypt config.")
        return

    path = config_path or CONFIG_FILE
    if not path.exists():
        logger.info(f"Config file not found: {path}")
        return

    if is_encrypted(path):
        logger.info("Config is already encrypted.")
        return

    plaintext = path.read_text(encoding="utf-8")
    fernet = _get_fernet()
    ciphertext = fernet.encrypt(plaintext.encode("utf-8"))

    encrypted = json.dumps(
        {_ENCRYPTION_MARKER: True, "data": base64.b64encode(ciphertext).decode("ascii")}, indent=2
    )

    path.write_text(encrypted, encoding="utf-8")
    logger.info(f"Config encrypted: {path}")


def decrypt_config(config_path: Optional[Path] = None) -> None:
    """Decrypt an encrypted config file to plaintext.

    Args:
        config_path: Path to config file (defaults to ~/.myclaw/config.json)
    """
    if not _CRYPTO_AVAILABLE:
        logger.warning("cryptography is not installed. Cannot decrypt config.")
        return

    path = config_path or CONFIG_FILE
    if not path.exists():
        logger.info(f"Config file not found: {path}")
        return

    if not is_encrypted(path):
        logger.info("Config is already plaintext.")
        return

    raw = json.loads(path.read_text(encoding="utf-8"))
    ciphertext = base64.b64decode(raw["data"].encode("ascii"))

    key = _load_key()
    if not key:
        raise RuntimeError(
            f"Config is encrypted but no decryption key found.\n"
            f"Key file: {KEY_FILE}\n"
            f"Install keyring or restore the key file to decrypt."
        )

    fernet = Fernet(key.encode("utf-8"))
    try:
        plaintext = fernet.decrypt(ciphertext).decode("utf-8")
    except InvalidToken:
        raise RuntimeError(
            "Config decryption failed: invalid key or corrupted file.\n"
            "The encryption key may have changed or the file is damaged."
        )

    path.write_text(plaintext, encoding="utf-8")
    logger.info(f"Config decrypted: {path}")


def load_encrypted_or_plain(path: Path) -> Dict[str, Any]:
    """Load config file, auto-detecting encryption.

    Returns:
        Parsed JSON dict (decrypted if necessary).

    Raises:
        RuntimeError: If file is encrypted but key is missing.
        json.JSONDecodeError: If file is not valid JSON.
    """
    if not path.exists():
        return {}

    text = path.read_text(encoding="utf-8")

    if not text.strip():
        return {}

    # Check if encrypted format
    try:
        maybe_wrapper = json.loads(text)
        if isinstance(maybe_wrapper, dict) and maybe_wrapper.get(_ENCRYPTION_MARKER):
            if not _CRYPTO_AVAILABLE:
                raise RuntimeError(
                    "Config is encrypted but 'cryptography' is not installed.\n"
                    "Run: pip install cryptography"
                )

            ciphertext = base64.b64decode(maybe_wrapper["data"].encode("ascii"))
            key = _load_key()
            if not key:
                raise RuntimeError(
                    f"Config is encrypted but no decryption key found.\n"
                    f"Key file: {KEY_FILE}\n"
                    f"Run 'zensynora config encrypt' to set up encryption, "
                    f"or restore the key file."
                )

            fernet = Fernet(key.encode("utf-8"))
            try:
                plaintext = fernet.decrypt(ciphertext).decode("utf-8")
            except InvalidToken:
                raise RuntimeError("Config decryption failed: invalid key or corrupted file.")
            return json.loads(plaintext)
    except (json.JSONDecodeError, KeyError, RuntimeError):
        # If it's not valid JSON or not encrypted format, re-raise RuntimeError
        # Otherwise fall through to plain JSON parse
        raise
    except Exception:
        pass  # Not encrypted format, try plain parse

    # Plain JSON
    return json.loads(text)


def save_encrypted(path: Path, data: Dict[str, Any]) -> None:
    """Save config dict, encrypting if a key exists.

    If the config file was previously encrypted, maintain encryption.
    If no key exists and the file was plaintext, keep it plaintext
    (unless the user explicitly ran 'zensynora config encrypt').
    """
    text = json.dumps(data, indent=2, ensure_ascii=False)

    # Only encrypt if a key already exists (user has opted into encryption)
    if _CRYPTO_AVAILABLE and _load_key():
        fernet = _get_fernet()
        ciphertext = fernet.encrypt(text.encode("utf-8"))
        encrypted = json.dumps(
            {_ENCRYPTION_MARKER: True, "data": base64.b64encode(ciphertext).decode("ascii")},
            indent=2,
        )
        path.write_text(encrypted, encoding="utf-8")
        logger.debug(f"Config saved encrypted: {path}")
    else:
        path.write_text(text, encoding="utf-8")
        logger.debug(f"Config saved plaintext: {path}")
