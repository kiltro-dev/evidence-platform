"""Atlas Live oído: streaming ASR (WebSocket) + file fallback (POST)."""

from .asr_file import (
    FILE_URL,
    file_response_to_bus_events,
    transcribe_file,
)
from .asr_ws import (
    ASR_MODEL,
    CHUNK_MS,
    SAMPLE_RATE,
    WS_URL,
    ASRConfig,
    ASRSession,
    PCMFeed,
    SessionStats,
    TurnTracker,
    build_handshake,
    bus_event,
    pcm_chunks_from_wav,
    transcribe_stream,
)

__all__ = [
    "ASR_MODEL",
    "CHUNK_MS",
    "FILE_URL",
    "SAMPLE_RATE",
    "WS_URL",
    "ASRConfig",
    "ASRSession",
    "PCMFeed",
    "SessionStats",
    "TurnTracker",
    "build_handshake",
    "bus_event",
    "file_response_to_bus_events",
    "pcm_chunks_from_wav",
    "transcribe_file",
    "transcribe_stream",
]
