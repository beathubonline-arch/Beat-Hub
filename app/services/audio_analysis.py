"""BeatHub audio analysis helpers.

Automatic BPM detection for uploaded audio files.
The analyzer is intentionally isolated from storage/database code so it can
be reused by upload and future audio-analysis features without changing the
Track model or existing purchase flow.
"""

from __future__ import annotations

from typing import BinaryIO, Optional


MIN_BPM = 40
MAX_BPM = 240
ANALYSIS_SECONDS = 90
SAMPLE_RATE = 22050


class BPMDetectionError(Exception):
    """Raised when BPM analysis cannot be completed."""


def _normalise_tempo(value) -> Optional[float]:
    try:
        if hasattr(value, "item"):
            value = value.item()
        tempo = float(value)
    except (TypeError, ValueError):
        return None

    if tempo <= 0:
        return None

    # Beat trackers can report half/double-time values. Bring obvious
    # outliers into the normal musical BPM range while preserving values
    # that already sit in the expected range.
    while tempo < MIN_BPM:
        tempo *= 2.0

    while tempo > MAX_BPM:
        tempo /= 2.0

    if tempo < MIN_BPM or tempo > MAX_BPM:
        return None

    return tempo


def detect_bpm(file_obj: BinaryIO) -> Optional[int]:
    """Detect the most likely musical BPM from a seekable audio file.

    Only the first ANALYSIS_SECONDS seconds are decoded. This keeps upload
    processing practical on Render while still providing enough material for
    reliable tempo estimation on normal songs, beats and DJ mixes.

    The file pointer is restored to position 0 before returning or raising.
    """

    try:
        import librosa
    except ImportError as exc:
        raise BPMDetectionError(
            "Automatic BPM detection is not installed. "
            "Add librosa and soundfile to requirements.txt and redeploy."
        ) from exc

    try:
        file_obj.seek(0)

        y, sr = librosa.load(
            file_obj,
            sr=SAMPLE_RATE,
            mono=True,
            duration=ANALYSIS_SECONDS,
        )

        if y is None or len(y) < sr * 5:
            return None

        tempo, _beats = librosa.beat.beat_track(
            y=y,
            sr=sr,
            trim=True,
            units="frames",
        )

        # librosa versions can return either a scalar or a one-element array.
        if hasattr(tempo, "reshape"):
            flattened = tempo.reshape(-1)
            candidates = list(flattened)
        else:
            candidates = [tempo]

        for candidate in candidates:
            normalised = _normalise_tempo(candidate)
            if normalised is not None:
                return int(round(normalised))

        return None

    except BPMDetectionError:
        raise
    except Exception as exc:
        raise BPMDetectionError(
            f"Audio BPM analysis failed: {exc}"
        ) from exc
    finally:
        try:
            file_obj.seek(0)
        except Exception:
            pass
