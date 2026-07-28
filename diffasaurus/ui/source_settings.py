from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
)

from diffasaurus.core.settings import load_settings, save_settings


class ReportSourceSettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Report source")
        self.resize(680, 300)
        settings = load_settings()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 26)
        layout.setSpacing(14)
        title = QLabel("Choose the CSV database")
        title.setStyleSheet("font-size: 23px; font-weight: 850;")
        subtitle = QLabel(
            "Use this project's reports folder, or point Diffasaurus at an existing shared history."
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color:#8295a8;")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        self.local = QRadioButton("Local project reports")
        self.external = QRadioButton("Existing or shared reports folder")
        layout.addWidget(self.local)
        layout.addWidget(self.external)

        row = QHBoxLayout()
        self.path = QLineEdit(settings.get("external_reports_path", ""))
        self.path.setPlaceholderText("Select a folder containing dated CSV reports…")
        browse = QPushButton("Browse")
        browse.clicked.connect(self.browse)
        row.addWidget(self.path, 1)
        row.addWidget(browse)
        layout.addLayout(row)
        layout.addStretch()

        actions = QHBoxLayout()
        test = QPushButton("Test folder")
        cancel = QPushButton("Cancel")
        save = QPushButton("Save")
        save.setObjectName("primaryButton")
        test.clicked.connect(self.test_folder)
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self.save)
        actions.addWidget(test)
        actions.addStretch()
        actions.addWidget(cancel)
        actions.addWidget(save)
        layout.addLayout(actions)

        if settings.get("report_source") == "external":
            self.external.setChecked(True)
        else:
            self.local.setChecked(True)

    def browse(self):
        selected = QFileDialog.getExistingDirectory(
            self,
            "Select reports folder",
            self.path.text().strip() or str(Path.home()),
        )
        if selected:
            self.path.setText(selected)
            self.external.setChecked(True)

    def test_folder(self):
        path = Path(self.path.text().strip()).expanduser()
        if not path.is_dir():
            QMessageBox.warning(self, "Report source", "This folder is not available.")
            return
        QMessageBox.information(
            self,
            "Report source",
            f"Folder available · {len(list(path.glob('*.csv')))} CSV snapshots found.",
        )

    def save(self):
        external_path = self.path.text().strip()
        if self.external.isChecked() and not Path(external_path).expanduser().is_dir():
            QMessageBox.warning(self, "Report source", "Choose an available reports folder.")
            return
        save_settings(
            {
                "report_source": "external" if self.external.isChecked() else "local",
                "external_reports_path": external_path,
            }
        )
        self.accept()
