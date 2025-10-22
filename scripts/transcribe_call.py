#!/usr/bin/env python3
"""
Offline transcription helper for RCA call recordings.

This script uses the Vosk lightweight speech recognizer to transcribe
WAV files captured during `scripts/rca_collect.sh` runs.  It downloads a
small 16 kHz English model on first use (≈50 MB) and caches it under
`models/vosk-model-small-en-us-0.15/`.

Examples:
    python scripts/transcribe_call.py \
        logs/remote/rca-*/recordings/out-*.wav

    python scripts/transcribe_call.py \
        --json transcripts.json \
        logs/remote/rca-20251017-190904/recordings/out-*.wav
"""

from __future__ import annotations

import argparse
import audioop
import json
import os
import tarfile
import urllib.request
import wave
from pathlib import Path
from typing import Dict, Iterable, List

from vosk import KaldiRecognizer, Model  # type: ignore

DEFAULT_MODEL_URL = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
DEFAULT_MODEL_DIR = Path("models/vosk-model-small-en-us-0.15")


def ensure_model(model_dir: Path, model_url: str = DEFAULT_MODEL_URL) -> Path:
    if model_dir.exists():
        return model_dir
    model_dir.parent.mkdir(parents=True, exist_ok=True)
    archive_path = model_dir.parent / (model_dir.name + ".zip")
    if not archive_path.exists():
        print(f"Downloading Vosk model to {archive_path} ...")
        urllib.request.urlretrieve(model_url, archive_path)
    print(f"Extracting {archive_path} ...")
    import zipfile

    with zipfile.ZipFile(archive_path, "r") as zf:
        zf.extractall(model_dir.parent)
    return model_dir


def _prepare_audio(path: Path, target_rate: int = 16000) -> bytes:
    with wave.open(str(path), "rb") as wf:
        channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())

    # Convert to PCM16 mono
    audio = frames
    if sampwidth == 1:
        audio = audioop.ulaw2lin(audio, 2)
        sampwidth = 2
    if channels > 1:
        audio = audioop.tomono(audio, sampwidth, 1, 1)
    if rate != target_rate:
        audio, _ = audioop.ratecv(audio, sampwidth, 1, rate, target_rate, None)
        rate = target_rate
    if sampwidth != 2:
        audio = audioop.lin2lin(audio, sampwidth, 2)
    return audio


def transcribe_file(model: Model, wav_path: Path) -> Dict[str, object]:
    pcm = _prepare_audio(wav_path)
    rec = KaldiRecognizer(model, 16000)
    rec.SetWords(True)

    chunk_size = 4000
    text_segments: List[Dict[str, object]] = []
    for idx in range(0, len(pcm), chunk_size):
        chunk = pcm[idx : idx + chunk_size]
        if rec.AcceptWaveform(chunk):
            res = json.loads(rec.Result())
            if res.get("text"):
                text_segments.append(res)
    final_res = json.loads(rec.FinalResult())
    if final_res.get("text"):
        text_segments.append(final_res)

    transcript = " ".join(seg.get("text", "") for seg in text_segments).strip()
    words = []
    for seg in text_segments:
        if "result" in seg:
            words.extend(seg["result"])

    confidences = [w.get("conf") for w in words if isinstance(w.get("conf"), (int, float))]
    non_speech_tokens = [w for w in words if isinstance(w.get("word"), str) and w["word"].startswith('[')]

    summary = {
        "word_count": len(words),
        "confidence_avg": float(sum(confidences) / len(confidences)) if confidences else None,
        "confidence_min": float(min(confidences)) if confidences else None,
        "confidence_max": float(max(confidences)) if confidences else None,
        "non_speech_token_count": len(non_speech_tokens),
    }

    return {
        "file": str(wav_path),
        "transcript": transcript,
        "words": words,
        "summary": summary,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Transcribe RCA call recordings using Vosk.")
    parser.add_argument("inputs", nargs="+", help="WAV files or glob patterns.")
    parser.add_argument("--model-dir", default=str(DEFAULT_MODEL_DIR), help="Vosk model directory.")
    parser.add_argument("--model-url", default=DEFAULT_MODEL_URL, help="Override model download URL.")
    parser.add_argument("--json", dest="json_out", help="Write JSON transcripts to file.")
    args = parser.parse_args()

    files: List[Path] = []
    for pattern in args.inputs:
        matches = list(Path(".").glob(pattern)) if any(c in pattern for c in "*[]?") else [Path(pattern)]
        files.extend([m for m in matches if m.is_file()])

    if not files:
        print("No audio files found.")
        return 1

    model_path = ensure_model(Path(args.model_dir), args.model_url)
    print(f"Loading Vosk model from {model_path} ...")
    model = Model(str(model_path))

    transcripts = []
    for wav_path in sorted(files):
        print(f"\nTranscribing {wav_path} ...")
        try:
            result = transcribe_file(model, wav_path)
        except Exception as exc:
            print(f"  [!] Failed to transcribe: {exc}")
            continue
        transcripts.append(result)
        print(f"  Transcript: {result['transcript'] or '(no speech detected)'}")

    if args.json_out:
        try:
            with open(args.json_out, "w") as jf:
                json.dump(transcripts, jf, indent=2)
            print(f"\nWrote transcripts to {args.json_out}")
        except Exception as exc:
            print(f"Failed to write JSON output: {exc}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

