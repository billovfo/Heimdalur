# heimdal/modules/shared/inputs.py
"""Request-input bounds (audit F-6): malformed user input is a 400, never a 500."""
from datetime import date

from flask import abort, request


def reject_nul():
    """before_request: Postgres text cannot hold NUL, so a NUL in any query or
    form value would reach a SQL parameter and raise DataError (500)."""
    for values in (request.args, request.form):
        if any("\x00" in v for v in values.values()):
            abort(400)


def page_arg():
    """?page= as an int >= 1 (junk -> 1). Clamp it with clamp_offset once the total is known."""
    return max(1, request.args.get("page", 1, type=int))


def clamp_offset(offset, total, per_page):
    """The entities-list clamp: a huge ?page= lands on the last page, never a huge OFFSET."""
    return min(offset, max(0, -(-total // per_page) - 1) * per_page)


def date_arg(name):
    """Optional ISO date arg -> 'YYYY-MM-DD' or None; anything else -> 400."""
    raw = request.args.get(name)
    if not raw:
        return None
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError:
        abort(400)


def json_object():
    """The request body as a JSON object; a list, scalar or bad JSON -> 400."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400)
    return data
