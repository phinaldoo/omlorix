def merge_settings_update(existing_settings: dict | None, incoming_settings: dict) -> dict:
    """Merge a partial settings update, treating explicit nulls as key removals."""
    merged_settings = dict(existing_settings) if isinstance(existing_settings, dict) else {}

    for key, value in incoming_settings.items():
        if value is None:
            merged_settings.pop(key, None)
            continue
        if isinstance(value, dict):
            current_value = merged_settings.get(key)
            nested_merged = merge_settings_update(
                current_value if isinstance(current_value, dict) else {},
                value,
            )
            if nested_merged:
                merged_settings[key] = nested_merged
            else:
                merged_settings.pop(key, None)
            continue
        merged_settings[key] = value

    return merged_settings
