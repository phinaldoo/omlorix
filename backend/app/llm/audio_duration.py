"""Safe duration measurement for uploaded dictation audio."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import wave


_MEDIA_PROBE_TIMEOUT_SECONDS = 15
_MAX_REPORTED_DURATION_SECONDS = 24 * 60 * 60


def _bounded_duration(value: object) -> float | None:
    """Convert a media-tool duration to a value accepted for quota accounting."""
    try:
        duration = float(value)
    except (TypeError, ValueError):
        return None
    return duration if 0 < duration <= _MAX_REPORTED_DURATION_SECONDS else None


def _measure_wav_duration(audio_bytes: bytes) -> float | None:
    """Measure a WAV payload using the standard library when possible."""
    try:
        with wave.open(io.BytesIO(audio_bytes), "rb") as reader:
            frame_rate = reader.getframerate()
            if frame_rate <= 0:
                return None
            return reader.getnframes() / frame_rate
    except (EOFError, wave.Error):
        return None


def _duration_from_ffprobe_output(stdout: str) -> float | None:
    """Extract the longest valid audio/container duration returned by ffprobe."""
    try:
        payload = json.loads(stdout or "{}")
    except json.JSONDecodeError:
        return None

    candidates = [_bounded_duration(payload.get("format", {}).get("duration"))]
    for stream in payload.get("streams", []):
        if not isinstance(stream, dict) or stream.get("codec_type") != "audio":
            continue
        candidates.append(_bounded_duration(stream.get("duration")))

    valid_candidates = [duration for duration in candidates if duration is not None]
    return max(valid_candidates) if valid_candidates else None


def _measure_packet_timestamps_with_ffmpeg(source_path: str) -> float | None:
    """Measure duration from real audio packet timestamps when metadata is absent.

    Browser MediaRecorder output may omit a container-level duration. Copying
    its audio packets through FFmpeg's null muxer avoids decoding them while
    still making FFmpeg report the final packet timestamp. The progress output
    stays small even for long recordings, unlike dumping every packet through
    ffprobe.
    """
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        return None
    try:
        result = subprocess.run(
            [
                ffmpeg_bin,
                "-nostdin",
                "-v",
                "error",
                "-progress",
                "pipe:1",
                "-i",
                source_path,
                "-map",
                "0:a:0",
                "-c",
                "copy",
                "-f",
                "null",
                os.devnull,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=_MEDIA_PROBE_TIMEOUT_SECONDS,
            text=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None

    # FFmpeg can emit more than one progress update. The greatest timestamp is
    # the completed stream duration and is expressed in microseconds.
    durations: list[float] = []
    for line in (result.stdout or "").splitlines():
        key, separator, value = line.partition("=")
        if separator and key == "out_time_us":
            duration = _bounded_duration_from_microseconds(value)
            if duration is not None:
                durations.append(duration)
    return max(durations) if durations else None


def _bounded_duration_from_microseconds(value: object) -> float | None:
    """Convert FFmpeg's integer microsecond timestamp to bounded seconds."""
    try:
        return _bounded_duration(float(value) / 1_000_000)
    except (TypeError, ValueError):
        return None


def _measure_with_ffprobe(audio_bytes: bytes, filename: str) -> float | None:
    """Measure browser audio with ffprobe and a packet-timestamp fallback."""
    ffprobe_bin = shutil.which("ffprobe")
    if not ffprobe_bin:
        return None
    suffix = Path(filename or "recording.webm").suffix or ".webm"
    with tempfile.NamedTemporaryFile(prefix="omlorix-dictation-", suffix=suffix) as source:
        source.write(audio_bytes)
        source.flush()
        try:
            result = subprocess.run(
                [
                    ffprobe_bin,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration:stream=codec_type,duration",
                    "-of",
                    "json",
                    source.name,
                ],
                # ffprobe does not support FFmpeg's ``-nostdin`` CLI option.
                # Disconnect stdin at the process level so it still cannot
                # wait for input while serving an HTTP request.
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=_MEDIA_PROBE_TIMEOUT_SECONDS,
                text=True,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode == 0:
            measured = _duration_from_ffprobe_output(result.stdout)
            if measured is not None:
                return measured

        # A valid WebM can have packet timestamps but no Duration element.
        # Measure those timestamps before treating the upload as unreadable.
        return _measure_packet_timestamps_with_ffmpeg(source.name)


def measure_audio_duration_seconds(
    audio_bytes: bytes,
    *,
    filename: str,
    reported_duration_seconds: float | None = None,
    allow_reported_duration: bool = False,
) -> float | None:
    """Return a bounded audio duration from trusted server-side metadata.

    Browser-reported duration is accepted only when the caller explicitly
    enables the compatibility fallback. Quota accounting leaves that fallback
    disabled so a client cannot under-report the duration it consumed.
    """
    measured = _measure_wav_duration(audio_bytes)
    if measured is None:
        measured = _measure_with_ffprobe(audio_bytes, filename)
    if measured is not None and 0 < measured <= _MAX_REPORTED_DURATION_SECONDS:
        return measured
    if not allow_reported_duration:
        return None
    try:
        reported = float(reported_duration_seconds) if reported_duration_seconds is not None else None
    except (TypeError, ValueError):
        return None
    if reported is None or not 0 < reported <= _MAX_REPORTED_DURATION_SECONDS:
        return None
    return reported
