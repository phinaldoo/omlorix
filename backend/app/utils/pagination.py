DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 200
MAX_PAGE_OFFSET = 10000


def merged_window_limit(limit: int, offset: int) -> int:
    return offset + limit + 1


def page_from_merged_window(items: list, *, limit: int, offset: int) -> tuple[list, bool]:
    page_end = offset + limit
    return items[offset:page_end], len(items) > page_end


def page_from_limited_items(items: list, *, limit: int) -> tuple[list, bool]:
    return items[:limit], len(items) > limit
