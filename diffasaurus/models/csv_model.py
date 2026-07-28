import csv, re
from pathlib import Path
from PyQt6.QtCore import Qt, QAbstractTableModel, QModelIndex

# ---------- CSV Model ----------
class CsvTableModel(QAbstractTableModel):
    def __init__(self, headers=None, rows=None):
        super().__init__()
        self._headers = headers or []
        self._rows = rows or []
        self._delimiter = ";"

    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(self._headers)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None

        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            r = index.row()
            c = index.column()
            try:
                val = self._rows[r][c]
            except IndexError:
                val = ""
            return val
        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None

        if orientation == Qt.Orientation.Horizontal:
            if 0 <= section < len(self._headers):
                return self._headers[section]
            return ""
        else:
            # Excel-like row numbers
            return str(section + 1)

    def load_csv(self, csv_path: Path):
        if not csv_path.exists():
            raise FileNotFoundError(str(csv_path))

        # Best-effort encoding handling
        encodings = ["utf-8-sig", "utf-8", "cp1252", "latin-1"]
        last_err = None

        for enc in encodings:
            try:
                with csv_path.open("r", encoding=enc, newline="") as f:
                    sample = f.read(8192)
                    sample = re.sub(r"\x00", "", sample)

                    try:
                        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
                    except csv.Error:
                        candidates = [";", ",", "\t"]
                        counts = {d: sample.count(d) for d in candidates}
                        best = max(counts, key=counts.get)
                        dialect = csv.excel
                        dialect.delimiter = best

                    self._delimiter = getattr(dialect, "delimiter", ";")

                    f.seek(0)
                    reader = csv.reader(f, dialect)
                    all_rows = list(reader)

                self.beginResetModel()
                if not all_rows:
                    self._headers = []
                    self._rows = []
                else:
                    self._headers = all_rows[0]
                    self._rows = all_rows[1:]
                self.endResetModel()
                return
            except Exception as e:
                last_err = e

        raise RuntimeError(f"Failed to read CSV with common encodings. Last error: {last_err}")
