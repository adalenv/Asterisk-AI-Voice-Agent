#!/usr/bin/env python3
"""
WAV Quality Analyzer

Analyze WAV recordings for call-quality troubleshooting and format alignment.

- Supports PCM16 WAV natively; attempts μ-law decode when sample width is 1.
- Computes header info, base stats (RMS, mean/DC, peak, clipping, zero-cross),
  frame-level RMS distribution, and simple heuristics/suggestions.
- Outputs console summary and optional JSON report.

Usage:
  python scripts/wav_quality_analyzer.py <files_or_globs...> [--json out.json] [--frame-ms 20]

Examples:
  python scripts/wav_quality_analyzer.py logs/remote/rca-*/recordings/*.wav --json wav_report.json

Notes:
- Only standard library; no numpy required.
- Spectral analysis can be added later if needed.
"""

from __future__ import annotations

import argparse
import audioop
import glob
import json
import math
import os
import struct
import statistics
import sys
import wave
from dataclasses import dataclass, asdict
from array import array
from pathlib import Path
from typing import Dict, List, Optional, Tuple


@dataclass
class FileHeader:
    path: str
    channels: int
    sampwidth: int
    rate: int
    frames: int
    duration_s: float
    comptype: str
    compname: str
    format_guess: str  # 'pcm16' | 'mulaw' | 'unknown'


@dataclass
class FrameStats:
    frame_ms: int
    frame_count: int
    rms_min: int
    rms_max: int
    rms_mean: float
    rms_stdev: float
    silence_frames: int
    silence_ratio: float


@dataclass
class BaseStats:
    rms: int
    mean: int
    peak: int
    zero_cross_rate: float
    clip_count: int
    clip_ratio: float


@dataclass
class AnalysisResult:
    header: FileHeader
    base: BaseStats
    frames: FrameStats
    metrics: Dict[str, object]
    recommendation: str


def _read_wav_header(path: str) -> Tuple[FileHeader, bytes]:
    with wave.open(path, 'rb') as w:
        nch = w.getnchannels()
        width = w.getsampwidth()
        rate = w.getframerate()
        frames = w.getnframes()
        comptype = w.getcomptype()
        compname = w.getcompname()
        duration_s = frames / rate if rate else 0.0
        raw = w.readframes(frames)

    # Guess encoding from header
    if width == 2 and (comptype == 'NONE' or comptype == 'NONE'):
        fmt = 'pcm16'
    elif width == 1:
        fmt = 'mulaw'  # heuristic; common for 8-bit μ-law WAV
    else:
        fmt = 'unknown'

    return (
        FileHeader(
            path=path,
            channels=nch,
            sampwidth=width,
            rate=rate,
            frames=frames,
            duration_s=duration_s,
            comptype=comptype,
            compname=compname,
            format_guess=fmt,
        ),
        raw,
    )


def _to_pcm16(header: FileHeader, raw: bytes) -> Tuple[bytes, str]:
    """Return PCM16-LE bytes for analysis and a mode label."""
    if not raw:
        return b"", 'empty'
    if header.sampwidth == 2:
        return raw, 'pcm16_native'
    if header.sampwidth == 1 or header.format_guess == 'mulaw':
        try:
            return audioop.ulaw2lin(raw, 2), 'decoded_ulaw_to_pcm16'
        except Exception:
            pass
    # Fallback: attempt μ-law decode anyway
    try:
        return audioop.ulaw2lin(raw, 2), 'decoded_ulaw_to_pcm16_fallback'
    except Exception:
        return raw, 'unknown_bytes'


def _analyze_base(pcm16: bytes, rate: int, clip_threshold: int = 32760) -> BaseStats:
    if not pcm16:
        return BaseStats(0, 0, 0, 0.0, 0, 0.0)
    rms = audioop.rms(pcm16, 2)
    mean = audioop.avg(pcm16, 2)
    peak = audioop.max(pcm16, 2)

    # Zero-crossing rate
    total = len(pcm16) // 2
    zc = 0
    if total:
        prev = None
        for (s,) in struct.iter_unpack('<h', pcm16):
            if prev is not None and ((s > 0) != (prev > 0)):
                zc += 1
            prev = s
    zcr = zc / max(1, total)

    # Clipping count
    clips = 0
    if total:
        for (s,) in struct.iter_unpack('<h', pcm16):
            if s >= clip_threshold or s <= -clip_threshold:
                clips += 1
    clip_ratio = clips / max(1, total)

    return BaseStats(rms=rms, mean=mean, peak=peak, zero_cross_rate=zcr, clip_count=clips, clip_ratio=clip_ratio)


def _analyze_frames(pcm16: bytes, rate: int, frame_ms: int = 20, silence_rms: int = 100) -> FrameStats:
    if not pcm16 or rate <= 0:
        return FrameStats(frame_ms, 0, 0, 0, 0.0, 0.0, 0, 0.0)

    bytes_per_frame = int(rate * (frame_ms / 1000.0) * 2)
    if bytes_per_frame <= 0:
        bytes_per_frame = 320 if rate == 8000 else max(2, rate // 25 * 2)

    rms_values: List[int] = []
    silence = 0
    offset = 0
    n = len(pcm16)
    while offset + bytes_per_frame <= n:
        frame = pcm16[offset: offset + bytes_per_frame]
        offset += bytes_per_frame
        r = audioop.rms(frame, 2)
        rms_values.append(r)
        if r < silence_rms:
            silence += 1

    if not rms_values:
        return FrameStats(frame_ms, 0, 0, 0, 0.0, 0.0, 0, 0.0)

    rms_min = min(rms_values)
    rms_max = max(rms_values)
    rms_mean = float(statistics.mean(rms_values)) if len(rms_values) > 1 else float(rms_values[0])
    rms_stdev = float(statistics.pstdev(rms_values)) if len(rms_values) > 1 else 0.0
    silence_ratio = silence / max(1, len(rms_values))

    return FrameStats(
        frame_ms=frame_ms,
        frame_count=len(rms_values),
        rms_min=rms_min,
        rms_max=rms_max,
        rms_mean=rms_mean,
        rms_stdev=rms_stdev,
        silence_frames=silence,
        silence_ratio=silence_ratio,
    )


def _estimate_snr(frame_stats: FrameStats, base_stats: BaseStats) -> Tuple[Optional[float], Optional[int]]:
    """Estimate SNR (dB) using frame-level RMS statistics.

    Uses the lower decile of RMS values as noise estimate and overall RMS as signal.
    Returns (snr_db, noise_rms).
    """

    if frame_stats.frame_count == 0 or base_stats.rms <= 0:
        return None, None

    try:
        # Approximate noise as rms_min or lower percentile using silence ratio.
        # Since we do not store full distribution, fall back to rms_min.
        noise_rms = max(1, frame_stats.rms_min)
        snr = 20 * math.log10(base_stats.rms / max(1.0, noise_rms))
        return snr, noise_rms
    except Exception:
        return None, None


def _spectral_centroid(pcm16: bytes, rate: int) -> Optional[float]:
    """Compute spectral centroid (Hz) using a simple DFT on the first window."""

    if not pcm16 or rate <= 0:
        return None

    samples = array('h')
    samples.frombytes(pcm16)
    if not samples:
        return None

    window_size = min(len(samples), 2048)
    if window_size < 64:
        return None

    segment = samples[:window_size]
    mags: List[float] = []
    for k in range(window_size // 2):
        real = 0.0
        imag = 0.0
        for n, sample in enumerate(segment):
            angle = -2.0 * math.pi * k * n / window_size
            real += sample * math.cos(angle)
            imag += sample * math.sin(angle)
        magnitude = math.hypot(real, imag)
        mags.append(magnitude)

    total_mag = sum(mags)
    if total_mag <= 0.0:
        return None

    centroid = 0.0
    for idx, magnitude in enumerate(mags):
        freq = idx * rate / window_size
        centroid += freq * magnitude
    centroid /= total_mag
    return centroid


def _detect_impairments(base: BaseStats, frames: FrameStats, snr_db: Optional[float]) -> List[str]:
    impairments: List[str] = []

    if snr_db is not None and snr_db < 15.0:
        impairments.append("low_snr")
    if base.clip_ratio > 0.005:
        impairments.append("clipping")
    if frames.silence_ratio > 0.8:
        impairments.append("high_silence_ratio")
    if abs(base.mean) > 500:
        impairments.append("dc_offset")

    return impairments


def _recommendation(header: FileHeader, base: BaseStats) -> str:
    rate = header.rate
    fmt = header.format_guess
    # Heuristic suggestions to minimize conversions.
    if rate == 8000:
        return (
            "Detected telephony 8 kHz. For minimal conversions, keep end-to-end μ-law @ 8000 Hz. "
            "If provider emits PCM16@24k (typical), convert to μ-law at the final egress only."
        )
    if rate in (16000, 24000):
        return (
            f"Detected PCM16-like {rate/1000:.0f} kHz. Align upstream to linear16@{rate} to avoid resampling, "
            "and only convert to μ-law@8k for PSTN egress, if needed."
        )
    return (
        f"Rate {rate} Hz ({fmt}). Verify upstream/downstream alignment. Prefer linear16@16k or μ-law@8k depending on path."
    )


def analyze_file(path: str, frame_ms: int = 20, clip_threshold: int = 32760) -> AnalysisResult:
    header, raw = _read_wav_header(path)
    pcm, mode = _to_pcm16(header, raw)
    base = _analyze_base(pcm, header.rate, clip_threshold=clip_threshold)
    frames = _analyze_frames(pcm, header.rate, frame_ms=frame_ms, silence_rms=100)
    snr_db, noise_rms = _estimate_snr(frames, base)
    spectral_centroid = _spectral_centroid(pcm, header.rate)
    dynamic_range = frames.rms_max - frames.rms_min if frames.frame_count else 0
    impairments = _detect_impairments(base, frames, snr_db)
    rec = _recommendation(header, base)
    metrics = {
        "snr_db": snr_db,
        "noise_rms": noise_rms,
        "spectral_centroid_hz": spectral_centroid,
        "dynamic_range": dynamic_range,
        "impairments": impairments,
        "analysis_mode": mode,
    }
    return AnalysisResult(header=header, base=base, frames=frames, metrics=metrics, recommendation=rec)


def main() -> int:
    p = argparse.ArgumentParser(description="Analyze WAV files for call quality and format alignment")
    p.add_argument("inputs", nargs="+", help="WAV file paths or globs")
    p.add_argument("--json", dest="json_out", help="Path to write JSON report", default=None)
    p.add_argument("--frame-ms", type=int, default=20, help="Frame size (ms) for frame-level stats (default 20)")
    p.add_argument("--clip-threshold", type=int, default=32760, help="PCM16 clip threshold (default 32760)")
    args = p.parse_args()

    # Expand globs
    files: List[str] = []
    for pat in args.inputs:
        matches = glob.glob(pat)
        if matches:
            files.extend(matches)
        else:
            files.append(pat)

    files = [f for f in files if os.path.isfile(f)]
    if not files:
        print("No input files found.")
        return 1

    results: List[Dict[str, object]] = []
    print(f"Analyzing {len(files)} file(s)\n")
    for idx, fpath in enumerate(sorted(files)):
        try:
            r = analyze_file(fpath, frame_ms=args.frame_ms, clip_threshold=args.clip_threshold)
        except wave.Error as e:
            print(f"[!] {fpath}: wave.Error: {e}")
            continue
        except Exception as e:
            print(f"[!] {fpath}: error: {e}")
            continue
        results.append(
            {
                "file": fpath,
                "header": asdict(r.header),
                "base": asdict(r.base),
                "frames": asdict(r.frames),
                "metrics": r.metrics,
                "recommendation": r.recommendation,
            }
        )
        # Console summary line
        h = r.header
        b = r.base
        fr = r.frames
        m = r.metrics
        snr_txt = f"SNR {m['snr_db']:.1f}dB" if m.get("snr_db") is not None else "SNR n/a"
        sc_txt = f"SC {m['spectral_centroid_hz']:.0f}Hz" if m.get("spectral_centroid_hz") else "SC n/a"
        flags = ",".join(m.get("impairments") or []) or "none"
        print(
            f"- {Path(fpath).name}: {h.rate} Hz, {h.sampwidth*8}-bit, {h.channels} ch, dur {h.duration_s:.2f}s | "
            f"RMS {b.rms}, mean {b.mean}, peak {b.peak}, clips {b.clip_count} ({b.clip_ratio:.5f}), ZCR {b.zero_cross_rate:.5f} | "
            f"frames {fr.frame_count} @ {fr.frame_ms}ms, RMSμ {fr.rms_mean:.1f}±{fr.rms_stdev:.1f}, silence {fr.silence_ratio*100:.1f}% | "
            f"{snr_txt}, {sc_txt}, flags: {flags}"
        )

    if args.json_out:
        try:
            with open(args.json_out, 'w') as jf:
                json.dump({"results": results}, jf, indent=2)
            print(f"\nWrote JSON report to {args.json_out}")
        except Exception as e:
            print(f"Failed to write JSON report: {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
