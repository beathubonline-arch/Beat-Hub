def _track_audio_storage_path(track: Track) -> Optional[str]:
    value = _model_value(
        track,
        "preview_file_path",
        "audio_preview_path",
        "audio_preview_url",
        "preview_url",
        "audio_url",
        "stream_url",
        "mp3_url",
        "preview_audio_url",
        "audio_file_path",   # IMPORTANT FALLBACK
        default=None,
    )

    if value is None:
        return None

    value = str(value).strip()
    return value or None
