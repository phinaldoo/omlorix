"""Profile-picture validation, storage, and response helpers.

Keeping image processing and filesystem concerns here makes the user settings
module easier to navigate while preserving the established public API through
``app.users.utils``.
"""

from PIL import Image, ImageOps, UnidentifiedImageError
from fastapi.responses import FileResponse
from datetime import datetime, timezone
from fastapi import HTTPException
from pathlib import Path
import logging
import io
import os
from typing import Any, Optional

from app.users.init import (
    get_user_setting_value,
    update_user_settings_bulk,
)
from app.groups.init import get_user_group_setting_value
from app.users.models import (
    get_user,
    update_user_profile_picture_boolean,
)
from app.settings.utils import (
    coerce_bool,
)

from app.paths import DATA_DIR
from app.users.upload_limits import (
    CUSTOM_PROFILE_PICTURE_MAX_BYTES,
    CUSTOM_PROFILE_PICTURE_MAX_SIZE_MB,
)

logger = logging.getLogger(__name__)


PROFILE_PICTURE_NO_STORE_HEADERS = {
    "Cache-Control": "no-store, no-cache, max-age=0, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
}

PROFILE_PICTURE_MAX_DIMENSION = 512
PROFILE_PICTURE_MAX_BYTES = 50 * 1024 * 1024
OAUTH_PROFILE_PICTURE_MAX_BYTES = 5 * 1024 * 1024
CUSTOM_PROFILE_PICTURE_DIR = DATA_DIR / "profilepicture"
OAUTH_PROFILE_PICTURE_DIR = DATA_DIR / "oauth_profilepicture"


SECONDARY_EXTENSION_DENYLIST = {
    "ade",
    "adp",
    "apk",
    "app",
    "asp",
    "aspx",
    "bat",
    "cmd",
    "com",
    "cpl",
    "dll",
    "dmg",
    "exe",
    "gad",
    "hta",
    "html",
    "jar",
    "js",
    "jse",
    "lnk",
    "msc",
    "msi",
    "msp",
    "pif",
    "pl",
    "ps1",
    "psm1",
    "py",
    "rb",
    "scr",
    "sh",
    "shtml",
    "vb",
    "vbe",
    "vbs",
    "ws",
    "wsc",
    "wsf",
    "wsh",
    "zip",
    "rar",
    "7z",
    "gz",
    "bz2",
    "xz",
    "tar",
}

PROFILE_PICTURE_FORMAT_TO_EXTENSION = {
    "jpeg": ".jpg",
    "jpg": ".jpg",
    "png": ".png",
    "gif": ".gif",
    "webp": ".webp",
}

DEFAULT_ALLOWED_IMAGE_EXTENSIONS = [".jpg", ".jpeg", ".png", ".gif", ".webp"]


def _profile_picture_dirs() -> tuple[Path, Path]:
    """Return the custom and OAuth profile picture directories."""
    return CUSTOM_PROFILE_PICTURE_DIR, OAUTH_PROFILE_PICTURE_DIR


def _list_profile_picture_files(profile_dir: Path, user_id: str) -> list[Path]:
    """List all profile picture files for a user in the given directory."""
    return list(profile_dir.glob(f"{user_id}.*"))


def _remove_profile_picture_files(profile_dir: Path, user_id: str) -> None:
    """Remove all profile picture files for a user from the given directory."""
    for existing_file in _list_profile_picture_files(profile_dir, user_id):
        try:
            existing_file.unlink()
        except OSError as exc:
            logger.warning(
                "Error removing profile picture file %s: %s", existing_file, exc
            )


def _resolve_profile_picture_file(user_id: str) -> tuple[Optional[Path], str]:
    """Resolve the active profile picture file path and source for a user."""
    for profile_dir, source in (
        (CUSTOM_PROFILE_PICTURE_DIR, "custom"),
        (OAUTH_PROFILE_PICTURE_DIR, "oauth"),
    ):
        profile_files = _list_profile_picture_files(profile_dir, user_id)
        if profile_files:
            return profile_files[0], source
    return None, "initials"


def get_profile_picture_status(user_id: str, db) -> dict[str, Any]:
    """Get profile-picture status without repairing or mutating user state."""
    user = get_user(db, user_id, None)
    oauth_present = bool(
        get_user_setting_value(
            user_id, "social_login", "oauth_profile_picture_present", db
        )
    )
    oauth_provider = str(
        get_user_setting_value(
            user_id, "social_login", "oauth_profile_picture_provider", db
        )
        or ""
    ).strip()
    oauth_sync_disabled = coerce_bool(
        get_user_setting_value(
            user_id, "social_login", "oauth_profile_picture_sync_disabled", db
        ),
        default=False,
    )

    if bool(getattr(user, "custom_profile_picture", False)):
        custom_file = _list_profile_picture_files(CUSTOM_PROFILE_PICTURE_DIR, user_id)
        if custom_file:
            return {
                "has_profile_picture": True,
                "has_custom_profile_picture": True,
                "profile_picture_source": "custom",
                "profile_picture_provider": "",
            }

    if oauth_present and not oauth_sync_disabled:
        oauth_file = _list_profile_picture_files(OAUTH_PROFILE_PICTURE_DIR, user_id)
        if oauth_file:
            return {
                "has_profile_picture": True,
                "has_custom_profile_picture": False,
                "profile_picture_source": "oauth",
                "profile_picture_provider": oauth_provider,
            }

    return {
        "has_profile_picture": False,
        "has_custom_profile_picture": False,
        "profile_picture_source": "initials",
        "profile_picture_provider": "",
    }


def clear_oauth_profile_picture(
    user_id: str, db, *, disable_sync: bool | None = None
) -> None:
    """Clear OAuth profile picture for a user."""
    _remove_profile_picture_files(OAUTH_PROFILE_PICTURE_DIR, user_id)
    social_login_updates = {
        "oauth_profile_picture_present": False,
        "oauth_profile_picture_provider": "",
        "oauth_profile_picture_last_synced_at": "",
    }
    if disable_sync is not None:
        social_login_updates["oauth_profile_picture_sync_disabled"] = bool(disable_sync)
    update_user_settings_bulk(
        user_id,
        {"social_login": social_login_updates},
        db,
    )


def _validate_and_prepare_profile_picture_bytes(
    *,
    file_content: bytes,
    original_filename: str | None = None,
    allowed_extensions: Optional[list[str]] = None,
    max_bytes: int = PROFILE_PICTURE_MAX_BYTES,
) -> tuple[bytes, str]:
    """Validate and prepare profile picture bytes, returning (bytes, extension)."""
    if len(file_content) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File size exceeds the {max_bytes // (1024 * 1024)} MB limit.",
        )

    normalized_allowed_extensions = None
    allowed_no_dot = None
    if allowed_extensions is not None:
        normalized_allowed_extensions = [
            ext.lower() if ext.startswith(".") else f".{ext.lower()}"
            for ext in allowed_extensions
        ]
        allowed_no_dot = {ext.lstrip(".") for ext in normalized_allowed_extensions}

    safe_name = None
    suffixes: list[str] = []
    original_extension = ""
    if original_filename:
        safe_name = os.path.basename(original_filename)
        if (
            safe_name != original_filename
            or safe_name.startswith(".")
            or ".." in safe_name
        ):
            raise HTTPException(status_code=400, detail="Invalid filename supplied.")
        suffixes = [s.lower() for s in Path(safe_name).suffixes]
        if suffixes:
            if len(suffixes) > 1:
                secondary_suffixes = [s.lstrip(".") for s in suffixes[:-1] if s]
                blocked = sorted(
                    {s for s in secondary_suffixes if s in SECONDARY_EXTENSION_DENYLIST}
                )
                if blocked:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Filename contains disallowed hidden extension segments: {', '.join(blocked)}.",
                    )
            original_extension = suffixes[-1]
    elif normalized_allowed_extensions:
        original_extension = normalized_allowed_extensions[0]

    try:
        img_bytes = io.BytesIO(file_content)
        img = Image.open(img_bytes)
        img_format = img.format
        img.verify()
        img_format = img_format.lower() if img_format else ""
    except UnidentifiedImageError as exc:
        raise HTTPException(
            status_code=400, detail="File is not a valid image."
        ) from exc
    except Exception as exc:
        # Verification failures include truncated files, malformed chunks, and
        # Pillow decompression-bomb protections. None should reach processing.
        logger.warning(
            "Rejected profile picture that failed structural verification",
            exc_info=True,
        )
        raise HTTPException(
            status_code=400, detail="File is not a valid image."
        ) from exc

    try:
        processed_img = Image.open(io.BytesIO(file_content))
    except Exception as exc:
        logger.warning(
            "Rejected profile picture that could not be decoded", exc_info=True
        )
        raise HTTPException(
            status_code=400, detail="File is not a valid image."
        ) from exc

    processed_img = ImageOps.exif_transpose(processed_img)
    is_animated = getattr(processed_img, "is_animated", False)

    resampling_attr = getattr(Image, "Resampling", None)
    resample_filter = resampling_attr.LANCZOS if resampling_attr else Image.LANCZOS

    if not is_animated and max(processed_img.size) > PROFILE_PICTURE_MAX_DIMENSION:
        processed_img.thumbnail(
            (PROFILE_PICTURE_MAX_DIMENSION, PROFILE_PICTURE_MAX_DIMENSION),
            resample=resample_filter,
        )

    output_bytes = file_content
    if not is_animated and img_format != "mpo":
        buffer = io.BytesIO()
        save_kwargs = {}
        format_upper = img_format.upper()

        if img_format in {"jpeg", "jpg"}:
            if processed_img.mode not in ("RGB", "L", "CMYK"):
                processed_img = processed_img.convert("RGB")
            save_kwargs.update(
                {
                    "format": "JPEG",
                    "quality": 85,
                    "optimize": True,
                    "progressive": True,
                }
            )
        elif img_format == "png":
            save_kwargs.update({"format": "PNG", "optimize": True})
        elif img_format == "webp":
            save_kwargs.update({"format": "WEBP", "quality": 80, "method": 6})
        elif img_format == "gif":
            save_kwargs.update({"format": "GIF"})
        else:
            save_kwargs["format"] = format_upper

        if "format" not in save_kwargs:
            save_kwargs["format"] = format_upper

        try:
            processed_img.save(buffer, **save_kwargs)
            output_bytes = buffer.getvalue()
        except Exception as exc:
            # Never retain the unprocessed upload here: it may still contain
            # metadata, trailing bytes, or dimensions the re-encode removes.
            logger.exception("Failed to safely re-encode profile picture")
            raise HTTPException(
                status_code=400,
                detail="The image could not be processed safely.",
            ) from exc

    if not img_format:
        raise HTTPException(status_code=400, detail="Could not determine image format.")
    if img_format not in PROFILE_PICTURE_FORMAT_TO_EXTENSION:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported or unrecognised image format: {img_format}. Supported formats: jpeg/jpg, png, gif, webp.",
        )

    file_extension = PROFILE_PICTURE_FORMAT_TO_EXTENSION[img_format]
    canonical_ext = file_extension.lstrip(".")

    if normalized_allowed_extensions and allowed_no_dot is not None:
        if canonical_ext not in allowed_no_dot and img_format not in allowed_no_dot:
            formatted_extensions = ", ".join(normalized_allowed_extensions)
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported image format. Allowed formats: {formatted_extensions}",
            )
        if (
            original_extension
            and original_extension not in normalized_allowed_extensions
        ):
            raise HTTPException(
                status_code=400,
                detail=f"File extension {original_extension} is not allowed.",
            )

    if original_extension and not (
        original_extension == file_extension
        or {original_extension, file_extension} <= {".jpg", ".jpeg"}
    ):
        raise HTTPException(
            status_code=400,
            detail=f"File extension {original_extension} does not match detected image format {file_extension}.",
        )

    return output_bytes, file_extension


def _store_profile_picture_bytes(
    *,
    user_id: str,
    file_content: bytes,
    file_extension: str,
    profile_dir: Path,
) -> Path:
    """Store profile picture bytes to the profile directory."""
    profile_dir.mkdir(parents=True, exist_ok=True)
    _remove_profile_picture_files(profile_dir, user_id)
    file_path = profile_dir / f"{user_id}{file_extension}"
    with open(file_path, "wb") as file_handle:
        file_handle.write(file_content)
    return file_path


def save_oauth_profile_picture(
    user_id: str,
    *,
    provider: str,
    file_content: bytes,
    original_filename: str | None,
    db,
) -> dict[str, Any]:
    """Save an OAuth profile picture for a user."""
    normalized_bytes, file_extension = _validate_and_prepare_profile_picture_bytes(
        file_content=file_content,
        original_filename=original_filename,
        allowed_extensions=list(PROFILE_PICTURE_FORMAT_TO_EXTENSION.values()),
        max_bytes=OAUTH_PROFILE_PICTURE_MAX_BYTES,
    )
    _store_profile_picture_bytes(
        user_id=user_id,
        file_content=normalized_bytes,
        file_extension=file_extension,
        profile_dir=OAUTH_PROFILE_PICTURE_DIR,
    )
    update_user_settings_bulk(
        user_id,
        {
            "social_login": {
                "oauth_profile_picture_present": True,
                "oauth_profile_picture_provider": str(provider or "").strip().lower(),
                "oauth_profile_picture_last_synced_at": datetime.now(
                    timezone.utc
                ).isoformat(),
            }
        },
        db,
    )
    return {"status": "success", "source": "oauth"}


# -------------------
# Upload Profile Picture
# -------------------
def upload_profile_picture(user_id, file, db):
    """Validate, normalize, and store a user's custom profile picture."""
    # Check if custom profile picture is enabled
    if not get_user_group_setting_value(
        user_id, "users", "enable_custom_profile_picture", db
    ):
        raise HTTPException(
            status_code=409, detail="Custom profile picture is not enabled."
        )

    file.file.seek(0, os.SEEK_END)
    file_size_bytes = file.file.tell()
    file.file.seek(0)

    if file_size_bytes > CUSTOM_PROFILE_PICTURE_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File size exceeds the {CUSTOM_PROFILE_PICTURE_MAX_SIZE_MB} MB limit.",
        )
    file_content = file.file.read()

    filename = file.filename
    if not filename:
        raise HTTPException(status_code=400, detail="Filename must not be empty.")
    if not Path(filename).suffixes:
        raise HTTPException(
            status_code=400, detail="Uploaded file must include an extension."
        )

    normalized_bytes, file_extension = _validate_and_prepare_profile_picture_bytes(
        file_content=file_content,
        original_filename=filename,
        allowed_extensions=DEFAULT_ALLOWED_IMAGE_EXTENSIONS,
        max_bytes=CUSTOM_PROFILE_PICTURE_MAX_BYTES,
    )

    user = get_user(db, user_id, None)
    try:
        _store_profile_picture_bytes(
            user_id=user.id,
            file_content=normalized_bytes,
            file_extension=file_extension,
            profile_dir=CUSTOM_PROFILE_PICTURE_DIR,
        )
        user.custom_profile_picture = True
        db.commit()
        db.refresh(user)
    except Exception as exc:
        # Preserve the full failure for operators without exposing filesystem,
        # database, or image-library details through the API response.
        db.rollback()
        logger.exception("Failed to store profile picture for user %s", user_id)
        raise HTTPException(
            status_code=500, detail="Failed to save profile picture."
        ) from exc

    clear_oauth_profile_picture(user.id, db, disable_sync=True)
    return {"status": "success"}


# -------------------
# Delete Profile Picture
# -------------------
def delete_profile_picture(user_id, db):
    """Remove custom and OAuth avatars and disable automatic OAuth resync."""
    update_user_profile_picture_boolean(db, user_id, False)
    # Delete the profile picture file
    _remove_profile_picture_files(CUSTOM_PROFILE_PICTURE_DIR, user_id)
    clear_oauth_profile_picture(user_id, db, disable_sync=True)
    return {"status": "success"}


# -------------------
# Get Profile Picture
# -------------------
def get_profile_picture(user_id, db):
    """
    Get the profile picture for a user by user_id.
    Returns the file content if found, or an error response if not found.
    """
    try:
        user = get_user(db, user_id, None)
        file_path = None

        if bool(getattr(user, "custom_profile_picture", False)):
            custom_files = _list_profile_picture_files(
                CUSTOM_PROFILE_PICTURE_DIR, user_id
            )
            file_path = custom_files[0] if custom_files else None

        if file_path is None:
            oauth_present = bool(
                get_user_setting_value(
                    user_id, "social_login", "oauth_profile_picture_present", db
                )
            )
            oauth_sync_disabled = coerce_bool(
                get_user_setting_value(
                    user_id, "social_login", "oauth_profile_picture_sync_disabled", db
                ),
                default=False,
            )
            if oauth_present and not oauth_sync_disabled:
                oauth_files = _list_profile_picture_files(
                    OAUTH_PROFILE_PICTURE_DIR, user_id
                )
                file_path = oauth_files[0] if oauth_files else None

        if file_path is None:
            raise HTTPException(status_code=404, detail="User has no profile picture.")

        # Set appropriate content type based on file extension
        media_type = None
        suffix = file_path.suffix.lower()
        if suffix in [".jpg", ".jpeg"]:
            media_type = "image/jpeg"
        elif suffix == ".png":
            media_type = "image/png"
        elif suffix == ".gif":
            media_type = "image/gif"
        elif suffix == ".webp":
            media_type = "image/webp"

        return FileResponse(
            path=str(file_path),
            media_type=media_type,
            filename=f"profile{file_path.suffix}",
            headers=PROFILE_PICTURE_NO_STORE_HEADERS,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to retrieve profile picture for user %s", user_id)
        raise HTTPException(
            status_code=500, detail="Failed to retrieve profile picture."
        ) from exc


# -------------------
# Update User Personal Details
# -------------------
