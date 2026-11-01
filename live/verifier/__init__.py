"""Deterministic verifier for Atlas Live M2a (code only, no model)."""

from .check import ClaimInput, Verdict, normalize_ws, verify_and_emit, verify_claim

__all__ = ["ClaimInput", "Verdict", "normalize_ws", "verify_and_emit", "verify_claim"]
