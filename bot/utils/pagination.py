"""
Generic pagination utility for inline keyboards.
Slices item lists into pages and provides navigation metadata.
"""


def paginate(
    items: list,
    page: int = 0,
    per_page: int = 6,
) -> tuple[list, bool, bool]:
    """
    Paginate a list of items.

    Args:
        items: Full list of items to paginate.
        page: Current page number (0-indexed).
        per_page: Number of items per page.

    Returns:
        Tuple of (page_items, has_prev, has_next):
        - page_items: Items for the current page.
        - has_prev: Whether a previous page exists.
        - has_next: Whether a next page exists.
    """
    if not items:
        return [], False, False

    total_pages = (len(items) + per_page - 1) // per_page
    page = max(0, min(page, total_pages - 1))

    start = page * per_page
    end = start + per_page
    page_items = items[start:end]

    has_prev = page > 0
    has_next = page < total_pages - 1

    return page_items, has_prev, has_next
