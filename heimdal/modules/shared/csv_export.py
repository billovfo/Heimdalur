"""CSV download helper for export endpoints."""
import csv
import io

from flask import Response

# F-9: a spreadsheet runs a cell that starts with one of these as a formula.
# Same rule as the client-side helper `heimdalDownloadCsv` in static/app.js.
FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def csv_cell(value):
    """Prefix ' to a string cell a spreadsheet would read as a formula.
    The csv module doubles embedded quotes itself."""
    if isinstance(value, str) and value.startswith(FORMULA_START):
        return "'" + value
    return value


def csv_response(rows, fieldnames, filename):
    """Build a CSV download Response from rows (dicts) and fieldnames."""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction='ignore')
    writer.writeheader()
    for r in rows:
        writer.writerow({k: csv_cell(v) for k, v in dict(r).items()})
    return Response(
        output.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )
