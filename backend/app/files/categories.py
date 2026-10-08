"""One SQL classification shared by paginated filtering and dynamic counts."""

from sqlalchemy import case, func

from app.files.models import Files


def file_category_expression():
    mime = func.lower(Files.file_type)
    canvas_type = Files.meta["canvas_type"].as_string()
    # The most specific type wins; generated slides belong with other slides.
    return case(
        (Files.meta["slide_presentation_source"].as_boolean().is_(True), "slides"),
        (
            mime.in_(
                (
                    "application/vnd.ms-powerpoint",
                    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                )
            ),
            "slides",
        ),
        (Files.meta["canvas"].as_boolean().is_(True), "canvas"),
        (canvas_type.isnot(None), "canvas"),
        (mime == "application/pdf", "pdf"),
        (mime.like("image/%"), "images"),
        (mime.like("audio/%"), "audio"),
        (mime.like("video/%"), "video"),
        (
            mime.in_(
                (
                    "text/csv",
                    "text/tab-separated-values",
                    "application/vnd.ms-excel",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    "application/vnd.oasis.opendocument.spreadsheet",
                )
            ),
            "spreadsheets",
        ),
        (
            mime.in_(
                (
                    "application/zip",
                    "application/x-tar",
                    "application/gzip",
                    "application/x-7z-compressed",
                )
            ),
            "archives",
        ),
        (mime.like("text/%"), "documents"),
        (Files.file_category == "document", "documents"),
        else_="other",
    )
