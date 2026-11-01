"""M3 vista: said-vs-shown vision checks for Atlas Live.

Submodules (standalone, stdlib + imageio_ffmpeg only)::

    live.vista.frames  — scene + periodic frame extraction (ffmpeg)
    live.vista.read    — vision read of one frame (strict JSON schema)
    live.vista.xcheck  — deterministic speech-vs-chart cross-check (no model)

See ``live/M3.md`` for the planted-chart matrix and the live 6-frame run.
"""

__all__ = ["frames", "read", "xcheck"]
