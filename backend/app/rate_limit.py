"""Shared slowapi limiter. Applied to auth (credential stuffing) and search
(protects the upstream sports-API budget) routes."""

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
