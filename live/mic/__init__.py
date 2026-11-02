"""Real-microphone live source for Atlas Live (F2-5)."""

from .capture import (
    BYTES_PER_SEC,
    CHANNELS,
    DRIVER_STUBBED_LABEL,
    SAMPLE_RATE,
    SAMPLE_WIDTH,
    SOURCE_LABEL,
    MicCapture,
    MicInput,
    NoMicrophoneError,
    candidate_binaries,
    custom_ffmpeg_input,
    describe_host,
    detect_microphone,
    driver_stubbed_file_input,
    file_test_input,
    lavfi_test_input,
)

__all__ = [
    "BYTES_PER_SEC",
    "CHANNELS",
    "DRIVER_STUBBED_LABEL",
    "SAMPLE_RATE",
    "SAMPLE_WIDTH",
    "SOURCE_LABEL",
    "MicCapture",
    "MicInput",
    "NoMicrophoneError",
    "candidate_binaries",
    "custom_ffmpeg_input",
    "describe_host",
    "detect_microphone",
    "driver_stubbed_file_input",
    "file_test_input",
    "lavfi_test_input",
]
