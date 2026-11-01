"""M6 auditor: internal canary claims + accuracy panel (branch live).

Canaries are INTERNAL calibration probes — never from the video, never
shown as video claims. They exercise the real pipeline
(``run_verify.run_claim`` -> store -> verifier) against World Bank facts
with ground truth pinned at authoring time and re-verifiable today via
the cited API URLs.
"""

from live.auditor.wilson import summarize, wilson_interval

__all__ = ["summarize", "wilson_interval"]
