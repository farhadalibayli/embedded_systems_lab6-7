from __future__ import annotations

import sys
import sqlite3
import datetime
from pathlib import Path

import serial
import serial.tools.list_ports
from PyQt6 import QtCore, QtGui, QtWidgets


DB_PATH = Path(__file__).with_name("tags.db")
DEFAULT_BAUD = 115200


# =========================================================================
# Database layer
# =========================================================================
class TagDB:
    """Thin SQLite wrapper for the tag log."""

    def __init__(self, path: Path):
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tags (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                uid         TEXT    NOT NULL UNIQUE,
                label       TEXT    DEFAULT '',
                scan_count  INTEGER NOT NULL DEFAULT 1,
                first_seen  TEXT    NOT NULL,
                last_seen   TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_tags_uid ON tags(uid);
            """
        )
        self.conn.commit()

    def upsert_scan(self, uid: str) -> tuple[int, int, bool]:
        """Insert new tag or bump scan_count. Returns (id, scan_count, is_new)."""
        uid = uid.strip().upper()
        now = datetime.datetime.now().isoformat(timespec="seconds")
        cur = self.conn.cursor()
        cur.execute("SELECT id, scan_count FROM tags WHERE uid = ?", (uid,))
        row = cur.fetchone()
        if row is None:
            cur.execute(
                "INSERT INTO tags(uid, scan_count, first_seen, last_seen) "
                "VALUES (?, 1, ?, ?)",
                (uid, now, now),
            )
            self.conn.commit()
            return cur.lastrowid, 1, True
        new_count = row["scan_count"] + 1
        cur.execute(
            "UPDATE tags SET scan_count = ?, last_seen = ? WHERE id = ?",
            (new_count, now, row["id"]),
        )
        self.conn.commit()
        return row["id"], new_count, False

    def all_rows(self) -> list[sqlite3.Row]:
        cur = self.conn.execute(
            "SELECT id, uid, label, scan_count, first_seen, last_seen "
            "FROM tags ORDER BY id ASC"
        )
        return cur.fetchall()

    def set_label(self, tag_id: int, label: str) -> None:
        self.conn.execute("UPDATE tags SET label = ? WHERE id = ?", (label, tag_id))
        self.conn.commit()

    def delete(self, tag_id: int) -> None:
        self.conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()


# =========================================================================
# Serial reader thread
# =========================================================================
class SerialReader(QtCore.QThread):
    """Reads lines from the Arduino on a worker thread.

    Emits one signal per line type so the GUI thread can react.
    """

    line_received  = QtCore.pyqtSignal(str)       # raw line, for log
    state_changed  = QtCore.pyqtSignal(str)       # WAITING / LOCKED / UNLOCKED
    tag_scanned    = QtCore.pyqtSignal(str)       # UID hex
    error_occurred = QtCore.pyqtSignal(str)

    def __init__(self, port: str, baud: int = DEFAULT_BAUD):
        super().__init__()
        self.port = port
        self.baud = baud
        self._stop = False

    def run(self) -> None:
        try:
            ser = serial.Serial(self.port, self.baud, timeout=0.5)
        except serial.SerialException as e:
            self.error_occurred.emit(f"Could not open {self.port}: {e}")
            return

        with ser:
            while not self._stop:
                try:
                    raw = ser.readline()
                except serial.SerialException as e:
                    self.error_occurred.emit(f"Serial read error: {e}")
                    return
                if not raw:
                    continue
                try:
                    line = raw.decode("utf-8", errors="replace").strip()
                except Exception:
                    continue
                if not line:
                    continue
                self.line_received.emit(line)
                if line.startswith("STATE,"):
                    self.state_changed.emit(line.split(",", 1)[1])
                elif line.startswith("TAG,"):
                    self.tag_scanned.emit(line.split(",", 1)[1])

    def stop(self) -> None:
        self._stop = True


# =========================================================================
# Main window
# =========================================================================
class MainWindow(QtWidgets.QMainWindow):
    COLUMNS = ["ID", "UID", "Label", "Scans", "First seen", "Last seen"]

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Lab 7 — RFID Tag Logger")
        self.resize(900, 560)

        self.db = TagDB(DB_PATH)
        self.reader: SerialReader | None = None

        self._build_ui()
        self._refresh_table()

    # ---- UI construction ----
    def _build_ui(self) -> None:
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)

        # Top bar: port + connect + state
        top = QtWidgets.QHBoxLayout()

        top.addWidget(QtWidgets.QLabel("Port:"))
        self.port_combo = QtWidgets.QComboBox()
        self.port_combo.setMinimumWidth(220)
        self._populate_ports()
        top.addWidget(self.port_combo)

        self.refresh_btn = QtWidgets.QPushButton("↻")
        self.refresh_btn.setFixedWidth(32)
        self.refresh_btn.setToolTip("Rescan serial ports")
        self.refresh_btn.clicked.connect(self._populate_ports)
        top.addWidget(self.refresh_btn)

        self.connect_btn = QtWidgets.QPushButton("Connect")
        self.connect_btn.clicked.connect(self._toggle_connection)
        top.addWidget(self.connect_btn)

        top.addStretch(1)

        top.addWidget(QtWidgets.QLabel("State:"))
        self.state_label = QtWidgets.QLabel("DISCONNECTED")
        self.state_label.setStyleSheet(
            "padding:4px 10px; border-radius:6px; "
            "background:#444; color:white; font-weight:bold;"
        )
        top.addWidget(self.state_label)

        root.addLayout(top)

        # Splitter: table on top, log on bottom
        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        root.addWidget(splitter, 1)

        # Table
        self.table = QtWidgets.QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.ResizeMode.Stretch
        )
        self.table.horizontalHeader().setSectionResizeMode(
            0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            3, QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.DoubleClicked
            | QtWidgets.QAbstractItemView.EditTrigger.SelectedClicked
        )
        self.table.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setSortingEnabled(True)
        self.table.itemChanged.connect(self._on_item_changed)
        splitter.addWidget(self.table)

        # Bottom: action buttons + log
        bottom_widget = QtWidgets.QWidget()
        bottom = QtWidgets.QVBoxLayout(bottom_widget)
        bottom.setContentsMargins(0, 0, 0, 0)

        actions = QtWidgets.QHBoxLayout()
        self.delete_btn = QtWidgets.QPushButton("Delete selected")
        self.delete_btn.clicked.connect(self._delete_selected)
        actions.addWidget(self.delete_btn)
        self.refresh_table_btn = QtWidgets.QPushButton("Refresh table")
        self.refresh_table_btn.clicked.connect(self._refresh_table)
        actions.addWidget(self.refresh_table_btn)
        actions.addStretch(1)
        actions.addWidget(QtWidgets.QLabel("Serial log:"))
        bottom.addLayout(actions)

        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(500)
        self.log.setStyleSheet(
            "font-family: Consolas, 'DejaVu Sans Mono', monospace; "
            "font-size: 11px;"
        )
        bottom.addWidget(self.log)

        splitter.addWidget(bottom_widget)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        # Status bar
        self.statusBar().showMessage("Ready")

    # ---- Serial port helpers ----
    def _populate_ports(self) -> None:
        self.port_combo.clear()
        for p in serial.tools.list_ports.comports():
            self.port_combo.addItem(f"{p.device} — {p.description}", p.device)
        if self.port_combo.count() == 0:
            self.port_combo.addItem("(no serial ports found)", None)

    def _toggle_connection(self) -> None:
        if self.reader is None:
            device = self.port_combo.currentData()
            if not device:
                QtWidgets.QMessageBox.warning(
                    self, "No port", "No serial port selected."
                )
                return
            self.reader = SerialReader(device, DEFAULT_BAUD)
            self.reader.line_received.connect(self._on_line)
            self.reader.state_changed.connect(self._on_state)
            self.reader.tag_scanned.connect(self._on_tag)
            self.reader.error_occurred.connect(self._on_serial_error)
            self.reader.finished.connect(self._on_reader_finished)
            self.reader.start()
            self.connect_btn.setText("Disconnect")
            self.statusBar().showMessage(f"Connected to {device}")
        else:
            self.reader.stop()
            self.reader.wait(1500)
            self.reader = None
            self.connect_btn.setText("Connect")
            self._set_state_label("DISCONNECTED")
            self.statusBar().showMessage("Disconnected")

    def _on_reader_finished(self) -> None:
        # Only act if the reader stopped on its own (e.g. error).
        if self.reader is not None:
            self.reader = None
            self.connect_btn.setText("Connect")
            self._set_state_label("DISCONNECTED")

    # ---- Serial signal handlers ----
    def _on_line(self, line: str) -> None:
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self.log.appendPlainText(f"[{ts}]  {line}")

    def _on_state(self, state: str) -> None:
        self._set_state_label(state)

    def _on_tag(self, uid: str) -> None:
        tag_id, count, is_new = self.db.upsert_scan(uid)
        msg = (
            f"NEW tag #{tag_id}: {uid}"
            if is_new
            else f"Tag #{tag_id} ({uid}) — scan #{count}"
        )
        self.statusBar().showMessage(msg, 4000)
        self._refresh_table()

    def _on_serial_error(self, msg: str) -> None:
        QtWidgets.QMessageBox.critical(self, "Serial error", msg)

    def _set_state_label(self, state: str) -> None:
        colors = {
            "WAITING":      ("#9a6a00", "WAITING"),
            "LOCKED":       ("#a30000", "LOCKED"),
            "UNLOCKED":     ("#1e7a1e", "UNLOCKED"),
            "DISCONNECTED": ("#444444", "DISCONNECTED"),
        }
        color, text = colors.get(state, ("#444", state))
        self.state_label.setText(text)
        self.state_label.setStyleSheet(
            f"padding:4px 10px; border-radius:6px; "
            f"background:{color}; color:white; font-weight:bold;"
        )

    # ---- Table management ----
    def _refresh_table(self) -> None:
        self.table.blockSignals(True)
        self.table.setSortingEnabled(False)
        rows = self.db.all_rows()
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            values = [
                str(row["id"]),
                row["uid"],
                row["label"] or "",
                str(row["scan_count"]),
                row["first_seen"],
                row["last_seen"],
            ]
            for c, v in enumerate(values):
                item = QtWidgets.QTableWidgetItem(v)
                if c != 2:  # only Label column is editable
                    item.setFlags(item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
                if c == 0:
                    item.setData(QtCore.Qt.ItemDataRole.UserRole, row["id"])
                if c in (0, 3):
                    item.setTextAlignment(
                        QtCore.Qt.AlignmentFlag.AlignCenter
                    )
                self.table.setItem(r, c, item)
        self.table.setSortingEnabled(True)
        self.table.blockSignals(False)

    def _on_item_changed(self, item: QtWidgets.QTableWidgetItem) -> None:
        if item.column() != 2:
            return
        id_item = self.table.item(item.row(), 0)
        if id_item is None:
            return
        tag_id = id_item.data(QtCore.Qt.ItemDataRole.UserRole)
        if tag_id is None:
            try:
                tag_id = int(id_item.text())
            except ValueError:
                return
        self.db.set_label(int(tag_id), item.text())
        self.statusBar().showMessage(f"Saved label for tag #{tag_id}", 2500)

    def _delete_selected(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        if not rows:
            return
        if QtWidgets.QMessageBox.question(
            self,
            "Delete tags",
            f"Delete {len(rows)} tag(s) from the database? This can't be undone.",
        ) != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        for r in rows:
            id_item = self.table.item(r, 0)
            if id_item is None:
                continue
            try:
                tag_id = int(id_item.text())
            except ValueError:
                continue
            self.db.delete(tag_id)
        self._refresh_table()

    # ---- Cleanup ----
    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        if self.reader is not None:
            self.reader.stop()
            self.reader.wait(1500)
        self.db.close()
        super().closeEvent(event)


def main() -> None:
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()