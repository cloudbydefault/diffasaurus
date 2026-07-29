from __future__ import annotations


def build_generic_stats(model, headers: list[str]) -> list[dict]:
    """Useful data-quality dashboard for every otherwise unknown CSV schema."""
    rows = model.rowCount()
    columns = model.columnCount()
    blank_cells = 0
    complete_rows = 0
    rows_with_blanks = 0
    for row in range(rows):
        values = getattr(model, "row_values", lambda _row: [])(row)
        if not values:
            values = [model.data(model.index(row, column)) for column in range(columns)]
        blanks = sum(
            not str(values[column] if column < len(values) else "").strip()
            for column in range(columns)
        )
        blank_cells += blanks
        if blanks:
            rows_with_blanks += 1
        else:
            complete_rows += 1
    total_cells = rows * columns
    completeness = (
        round(((total_cells - blank_cells) / total_cells) * 100, 1)
        if total_cells
        else 100.0
    )
    return [
        {
            "title": "Rows",
            "value": rows,
            "subtitle": "Snapshot records",
            "filter_spec": {},
            "kind": "info",
            "section": "Overview",
        },
        {
            "title": "Columns",
            "value": columns,
            "subtitle": "Schema fields",
            "kind": "accent",
            "section": "Overview",
        },
        {
            "title": "Completeness",
            "value": f"{completeness:.1f}%",
            "subtitle": "Non-blank cells",
            "kind": "good" if completeness >= 95 else "warning",
            "section": "Data quality",
        },
        {
            "title": "Complete rows",
            "value": complete_rows,
            "subtitle": "No blank fields",
            "kind": "good",
            "section": "Data quality",
        },
        {
            "title": "Rows with blanks",
            "value": rows_with_blanks,
            "subtitle": "At least one blank field",
            "kind": "warning",
            "section": "Data quality",
        },
        {
            "title": "Blank cells",
            "value": blank_cells,
            "subtitle": f"Across {total_cells:,} cells",
            "kind": "danger" if blank_cells else "good",
            "section": "Data quality",
        },
    ]
