"""Use signup's existing EmailStr canonical form without folding local-part case."""

from pydantic import EmailStr, TypeAdapter

_EMAIL = TypeAdapter(EmailStr)


def normalize_email(email: str) -> str:
    """Normalize a valid email using the same rules as the signup request model."""
    return _EMAIL.validate_python(email)
