from __future__ import annotations

import io
import wave

from .._logging import SdkLogger, create_sdk_logger


SILK_SAMPLE_RATE = 24_000
SILK_CHANNELS = 1
SILK_SAMPLE_WIDTH_BYTES = 2


def pcm_bytes_to_wav(pcm_bytes: bytes, sample_rate: int) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(SILK_CHANNELS)
        wav_file.setsampwidth(SILK_SAMPLE_WIDTH_BYTES)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm_bytes)
    return output.getvalue()


def silk_to_wav(silk_bytes: bytes, *, logger: SdkLogger | None = None) -> bytes | None:
    resolved_logger = logger or create_sdk_logger().child("silk")
    try:
        import pysilk

        resolved_logger.debug("silk_to_wav decoding silk_bytes=%s", len(silk_bytes))
        silk_stream = io.BytesIO(silk_bytes)
        pcm_stream = io.BytesIO()
        pysilk.decode(silk_stream, pcm_stream, SILK_SAMPLE_RATE)
        pcm_bytes = pcm_stream.getvalue()
        resolved_logger.debug("silk_to_wav decoded pcm_bytes=%s", len(pcm_bytes))
        wav_bytes = pcm_bytes_to_wav(pcm_bytes, SILK_SAMPLE_RATE)
        resolved_logger.debug("silk_to_wav encoded wav_bytes=%s", len(wav_bytes))
        return wav_bytes
    except Exception as exc:
        resolved_logger.warning("silk_to_wav failed, falling back to raw silk err=%s", exc)
        return None