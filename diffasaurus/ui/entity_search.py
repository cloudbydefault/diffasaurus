from __future__ import annotations

from PyQt6.QtCore import Qt, QStringListModel, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QCompleter,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from diffasaurus.core.entity.index_repository import EntityIndexRepository
from diffasaurus.core.entity.resolution import EntityResolver, SearchResult
from diffasaurus.core.entity.types import CanonicalEntityKey, EntityRecord, EntityType

ENTITY_TYPE_LABELS: dict[EntityType, str] = {
    "user": "User",
    "device": "Device",
    "shared_mailbox": "Shared mailbox",
}

ENTITY_TYPE_ORDER: tuple[EntityType, ...] = ("user", "device", "shared_mailbox")


class EntitySelectorPanel(QWidget):
    entity_selected = pyqtSignal(object)
    entity_type_changed = pyqtSignal()
    selection_cleared = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._resolver: EntityResolver | None = None
        self._repository: EntityIndexRepository | None = None
        self._selected: EntityRecord | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        controls = QVBoxLayout()
        controls.setSpacing(8)

        type_row = QVBoxLayout()
        type_row.setSpacing(4)
        type_label = QLabel("ENTITY TYPE")
        type_label.setObjectName("fieldLabel")
        self.type_combo = QComboBox()
        self.type_combo.setMinimumWidth(150)
        for entity_type in ENTITY_TYPE_ORDER:
            self.type_combo.addItem(ENTITY_TYPE_LABELS[entity_type], entity_type)
        type_row.addWidget(type_label)
        type_row.addWidget(self.type_combo)
        controls.addLayout(type_row)

        search_row = QVBoxLayout()
        search_row.setSpacing(4)
        search_label = QLabel("SEARCH")
        search_label.setObjectName("fieldLabel")
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("ID, UPN, device name, serial, SMTP address…")
        self._completer_model = QStringListModel()
        self._completer = QCompleter(self._completer_model)
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.search_input.setCompleter(self._completer)
        search_row.addWidget(search_label)
        search_row.addWidget(self.search_input)
        controls.addLayout(search_row)

        layout.addLayout(controls)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #8295a8; font-size: 12px;")
        self.status_label.setWordWrap(True)
        self.status_label.hide()
        layout.addWidget(self.status_label)

        self.disambiguation = QListWidget()
        self.disambiguation.setObjectName("disambiguationList")
        self.disambiguation.setMaximumHeight(120)
        self.disambiguation.hide()
        layout.addWidget(self.disambiguation)

        self.type_combo.currentIndexChanged.connect(self._entity_type_changed)
        self.search_input.returnPressed.connect(self._run_search)
        self.search_input.textChanged.connect(self._on_search_text_changed)
        self.disambiguation.itemClicked.connect(self._pick_disambiguation)
        self.disambiguation.itemDoubleClicked.connect(self._pick_disambiguation)

    @property
    def selected(self) -> EntityRecord | None:
        return self._selected

    def current_entity_type(self) -> EntityType:
        return self.type_combo.currentData()

    def set_repository(self, repository: EntityIndexRepository) -> None:
        self._repository = repository
        self._resolver = None
        self._selected = None
        self._update_completer()
        self.disambiguation.hide()
        capabilities = repository.search_capabilities()
        if capabilities.substring_search:
            ready_text = "Index ready. Search by ID, alias, or display name."
        else:
            ready_text = "Index ready. Prefix and exact search available (substring search unavailable)."
        self.status_label.setText(ready_text)
        self.status_label.show()

    def set_resolver(self, resolver: EntityResolver) -> None:
        self._repository = None
        self._resolver = resolver
        self._selected = None
        self._update_completer()
        self.disambiguation.hide()
        self.status_label.setText("Index ready. Search by ID, alias, or display name.")
        self.status_label.show()

    def show_indexing(self) -> None:
        self.status_label.setText("Building entity index from all snapshots…")
        self.status_label.show()
        self.disambiguation.hide()
        self._selected = None

    def show_index_progress(self, detail: str) -> None:
        self.status_label.setText(f"Building entity index… {detail}")
        self.status_label.show()

    def clear_index_state(self) -> None:
        self._selected = None
        self._completer_model.setStringList([])
        self.disambiguation.hide()
        self.status_label.hide()

    def show_sync_progress(self, detail: str) -> None:
        if self._repository is not None:
            self.status_label.setText(f"Updating index… {detail}")
            self.status_label.show()

    def show_index_error(self, message: str) -> None:
        if self._repository is not None:
            self.status_label.setText(f"Entity index sync issue: {message}")
            self.status_label.show()
            return
        self._repository = None
        self._resolver = None
        self._selected = None
        self._completer_model.setStringList([])
        self.disambiguation.hide()
        self.status_label.setText(f"Entity index failed: {message}")
        self.status_label.show()

    def select_record(self, record: EntityRecord, query: str | None = None) -> None:
        entity_type = record.key.entity_type
        for index in range(self.type_combo.count()):
            if self.type_combo.itemData(index) == entity_type:
                self.type_combo.setCurrentIndex(index)
                break
        if query:
            self.search_input.blockSignals(True)
            self.search_input.setText(query)
            self.search_input.blockSignals(False)
        else:
            self.search_input.blockSignals(True)
            self.search_input.setText(record.display_name)
            self.search_input.blockSignals(False)
        self._select_entity(record)

    def clear_selection(self) -> None:
        self._selected = None
        self.disambiguation.hide()
        self.status_label.hide()
        self.search_input.clear()

    def _entity_type_changed(self) -> None:
        self._selected = None
        self.disambiguation.hide()
        self._update_completer()
        self.status_label.hide()
        self.search_input.clear()
        self.selection_cleared.emit()
        self.entity_type_changed.emit()

    def _on_search_text_changed(self, text: str) -> None:
        self._update_completer()
        if self._selected is not None or self.disambiguation.isVisible():
            self._selected = None
            self.disambiguation.hide()
            self.selection_cleared.emit()

    def _update_completer(self) -> None:
        prefix = self.search_input.text().strip()
        if self._repository is not None:
            if prefix:
                suggestions = self._repository.autocomplete_prefix(prefix, self.current_entity_type())
            else:
                suggestions = []
            self._completer_model.setStringList(suggestions)
            return
        if not self._resolver:
            self._completer_model.setStringList([])
            return
        entity_type = self.current_entity_type()
        if not prefix:
            self._completer_model.setStringList([])
            return
        needle = prefix.casefold()
        suggestions: list[str] = []
        for record in self._resolver.records:
            if record.key.entity_type != entity_type:
                continue
            for value in (record.display_name, record.key.primary_id, *(a.value for a in record.aliases)):
                if value and value.casefold().startswith(needle):
                    suggestions.append(value)
        unique = sorted({value for value in suggestions if value}, key=str.casefold)
        self._completer_model.setStringList(unique[:2_000])

    def _record_for_key(self, key: CanonicalEntityKey) -> EntityRecord | None:
        if self._repository is not None:
            return self._repository.get_entity(key)
        if self._resolver is not None:
            return self._resolver.get(key)
        return None

    def _run_search(self) -> None:
        query = self.search_input.text().strip()
        if not query:
            return
        if self._repository is not None:
            result = self._repository.search(query, self.current_entity_type())
        elif self._resolver is not None:
            result = self._resolver.search(query, self.current_entity_type())
        else:
            return
        self.disambiguation.hide()
        self._selected = None
        self.selection_cleared.emit()
        if not result.matches:
            self.status_label.setText("No entity matches this search.")
            self.status_label.show()
            return
        if len(result.matches) == 1:
            self._select_entity(result.matches[0])
            return
        self._show_disambiguation(result)

    def _show_disambiguation(self, result: SearchResult) -> None:
        self.disambiguation.clear()
        for record in result.matches:
            item = QListWidgetItem(
                f"{record.display_name} · {record.key.primary_id} · last seen "
                f"{record.last_seen.strftime('%d %b %Y') if record.last_seen else '—'}"
            )
            item.setData(Qt.ItemDataRole.UserRole, record.key)
            self.disambiguation.addItem(item)
        self.disambiguation.show()
        self.status_label.setText("Multiple entities match this search. Choose one below.")
        self.status_label.show()

    def _pick_disambiguation(self, item: QListWidgetItem) -> None:
        key = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(key, CanonicalEntityKey):
            return
        record = self._record_for_key(key)
        if record is None:
            return
        self.disambiguation.hide()
        self._select_entity(record)

    def _select_entity(self, record: EntityRecord) -> None:
        self._selected = record
        self.status_label.hide()
        self.entity_selected.emit(record)
