"""Offline, bounded detected-face redaction; no claim that undetected faces are absent."""
from .redactor import LocalFaceRedactor, RedactionResult, RedactionUnavailable

__all__ = ["LocalFaceRedactor", "RedactionResult", "RedactionUnavailable"]
