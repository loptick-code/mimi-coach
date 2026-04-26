"""Chiffrement Fernet pour les tokens Garmin stockes en DB."""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


class CryptoError(RuntimeError):
    pass


def _get_fernet() -> Fernet:
    key = get_settings().fernet_key
    if not key:
        raise CryptoError(
            "FERNET_KEY n'est pas defini. Genere une cle avec :\n"
            '  python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"'
        )
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (ValueError, TypeError) as exc:
        raise CryptoError(f"FERNET_KEY invalide : {exc}") from exc


def encrypt(plaintext: str) -> bytes:
    return _get_fernet().encrypt(plaintext.encode())


def decrypt(ciphertext: bytes) -> str:
    try:
        return _get_fernet().decrypt(ciphertext).decode()
    except InvalidToken as exc:
        raise CryptoError(
            "Le token chiffre n'est pas dechiffrable avec la FERNET_KEY actuelle. "
            "L'utilisateur doit se reconnecter a Garmin."
        ) from exc
