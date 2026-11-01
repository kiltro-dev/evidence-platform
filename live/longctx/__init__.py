"""M7 long-context self-contradiction check for Atlas Live.

One long-context model call proposes speaker self-contradiction pairs
over the FULL transcript; deterministic code verifies every pair
(literal quotes, >30s separation, numeric/entity collision) before
any ``speaker.contradiction`` event is published.
"""

from live.longctx.check import (
    LongCtxConfig,
    Segment,
    load_srt,
    parse_srt,
    run_longctx,
    verify_pairs,
)

__all__ = [
    "LongCtxConfig",
    "Segment",
    "load_srt",
    "parse_srt",
    "run_longctx",
    "verify_pairs",
]
