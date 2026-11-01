"""HalluBench: hallucination-resistance ablation (judge-only vs judge+Atlas).

Public contract: CASES is a frozen list of claim dicts. See cases.py.
Model calls live ONLY in run_ablation.py; importing this package is
always offline and free.
"""

from .cases import CASES, CATEGORIES

__all__ = ["CASES", "CATEGORIES"]
