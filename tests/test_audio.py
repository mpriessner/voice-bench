"""Unit tests for voice_bench.audio.load_pcm16."""

import struct
import tempfile
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from voice_bench.audio import load_pcm16


def _write_wav(path: Path, data: np.ndarray, rate: int) -> None:
    sf.write(str(path), data, rate, subtype="PCM_16")


@pytest.fixture
def sine_16k(tmp_path: Path) -> Path:
    """1-second 440 Hz sine wave at 16kHz."""
    t = np.linspace(0, 1, 16000, endpoint=False)
    wave = (np.sin(2 * np.pi * 440 * t) * 32767).astype(np.int16)
    wav = tmp_path / "sine_16k.wav"
    _write_wav(wav, wave, 16000)
    return wav


def test_load_pcm16_no_resample(sine_16k: Path) -> None:
    """Loading at native rate returns expected byte count."""
    data = load_pcm16(sine_16k, target_rate=16000)
    assert len(data) == 16000 * 2  # 1 second × 2 bytes/sample


def test_load_pcm16_upsample_to_24k(sine_16k: Path) -> None:
    """Upsampling 16kHz → 24kHz scales byte count by 1.5×."""
    data = load_pcm16(sine_16k, target_rate=24000)
    expected = 24000 * 2
    # Allow ±0.5% tolerance for resampler edge handling
    assert abs(len(data) - expected) / expected < 0.005


def test_load_pcm16_returns_bytes(sine_16k: Path) -> None:
    assert isinstance(load_pcm16(sine_16k, target_rate=16000), bytes)


def test_load_pcm16_stereo_downmix(tmp_path: Path) -> None:
    """Stereo WAV is downmixed to mono before resampling."""
    t = np.linspace(0, 0.5, 8000, endpoint=False)
    left = (np.sin(2 * np.pi * 440 * t) * 32767).astype(np.int16)
    right = (np.sin(2 * np.pi * 880 * t) * 32767).astype(np.int16)
    stereo = np.stack([left, right], axis=1)
    wav = tmp_path / "stereo.wav"
    _write_wav(wav, stereo, 16000)
    data = load_pcm16(wav, target_rate=16000)
    # 0.5s × 16000 samples/s × 2 bytes/sample
    assert len(data) == 16000 * 2 // 2
