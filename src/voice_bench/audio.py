"""Shared audio loading utility for voice-bench adapters."""

from pathlib import Path

import numpy as np
import soundfile as sf


def load_pcm16(wav_path: Path, target_rate: int) -> bytes:
    """Read a WAV file and return raw PCM16 bytes resampled to target_rate, mono."""
    data, src_rate = sf.read(str(wav_path), dtype="int16", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1).astype(np.int16)

    if src_rate != target_rate:
        from fractions import Fraction
        from scipy.signal import resample_poly

        ratio = Fraction(target_rate, src_rate).limit_denominator(100)
        data = resample_poly(data.astype(np.float32), ratio.numerator, ratio.denominator)
        data = np.clip(data, -32768, 32767).astype(np.int16)

    return data.tobytes()
