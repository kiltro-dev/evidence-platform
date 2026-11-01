"""Real-microphone live source for Atlas Live (F2-5)."""

from .capture import (
    BYTES_PER_SEC,
    CHANNELS,
    SAMPLE_RATE,
    SAMPLE_WIDTH,
    SOURCE_LABEL,
    MicCapture,
    MicInput,
    NoMicrophoneError,
    candidate_binaries,
    describe_host,
    detect_microphone,
    file_test_input,
    lavfi_test_input,
)

__all__ = [
    "BYTES_PER_SEC",
    "CHANNELS",
    "SAMPLE_RATE",
    "SAMPLE_WIDTH",
    "SOURCE_LABEL",
    "MicCapture",
    "MicInput",
    "NoMicrophoneError",
    "candidate_binaries",
    "describe_host",
    "detect_microphone",
    "file_test_input",
    "lavfi_test_input",
]
