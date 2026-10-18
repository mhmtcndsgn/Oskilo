"""Küçük pencereler: kanal birimleri ve hızlı komut butonları."""
from __future__ import annotations

import math

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
                               QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout)

from .. import analysis
from ..analysis import parse_hex
from ..i18n import T


class UnitsDialog(QDialog):
    """Kanal başına: gerçek değer = ham × çarpan + ofset, birim adı."""

    def __init__(self, parent, lang: str, channels: list[str], units: dict):
        super().__init__(parent)
        t = self.t = T(lang)
        self.setWindowTitle(t("units_title"))
        self.setMinimumSize(560, 360)
        lay = QVBoxLayout(self)
        info = QLabel(t("units_help"))
        info.setWordWrap(True)
        info.setObjectName("Hint")
        lay.addWidget(info)

        names = list(dict.fromkeys(list(channels) + [n for n in units if n not in channels]))
        self.table = QTableWidget(len(names), 4)
        self.table.setHorizontalHeaderLabels([t("u_col_channel"), t("u_col_scale"), t("u_col_offset"), t("u_col_unit")])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for r, name in enumerate(names):
            u = units.get(name, {})
            it = QTableWidgetItem(name)
            it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(r, 0, it)
            self.table.setItem(r, 1, QTableWidgetItem(f"{u.get('scale', 1.0):.10g}"))
            self.table.setItem(r, 2, QTableWidgetItem(f"{u.get('offset', 0.0):.10g}"))
            self.table.setItem(r, 3, QTableWidgetItem(str(u.get("unit", ""))))
        lay.addWidget(self.table, 1)
        if not names:
            empty = QLabel(t("u_nochan"))
            empty.setWordWrap(True)
            lay.addWidget(empty)

        row = QHBoxLayout()
        b_adc = QPushButton(t("u_adc"))
        b_adc.clicked.connect(self._adc)
        b_reset = QPushButton(t("u_reset"))
        b_reset.clicked.connect(self._reset_rows)
        row.addWidget(b_adc)
        row.addWidget(b_reset)
        row.addStretch(1)
        lay.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Save).setText(t("u_save"))
        bb.button(QDialogButtonBox.Save).setObjectName("Accent")
        bb.button(QDialogButtonBox.Cancel).setText(t("cancel"))
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _rows(self) -> list[int]:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return rows or list(range(self.table.rowCount()))

    def _adc(self) -> None:
        t = self.t
        vref, ok = QInputDialog.getDouble(self, t("u_adc"), t("u_adc_vref"), 3.3, 0.001, 1000, 4)
        if not ok:
            return
        bits, ok = QInputDialog.getInt(self, t("u_adc"), t("u_adc_bits"), 12, 1, 32)
        if not ok:
            return
        for r in self._rows():
            self.table.item(r, 1).setText(f"{analysis.adc_scale(vref, bits):.10g}")
            self.table.item(r, 2).setText("0")
            self.table.item(r, 3).setText("V")

    def _reset_rows(self) -> None:
        for r in self._rows():
            self.table.item(r, 1).setText("1")
            self.table.item(r, 2).setText("0")
            self.table.item(r, 3).setText("")

    def result_units(self) -> dict:
        out = {}
        for r in range(self.table.rowCount()):
            name = self.table.item(r, 0).text()
            try:
                scale = float(self.table.item(r, 1).text().replace(",", "."))
                offset = float(self.table.item(r, 2).text().replace(",", "."))
            except ValueError:
                continue
            unit = self.table.item(r, 3).text().strip()[:16]
            u = analysis.unit_of({name: {"scale": scale, "offset": offset, "unit": unit}}, name)
            if u:
                out[name] = u
        return analysis.sanitize_units(out)


class QuickCmdDialog(QDialog):
    """Hızlı komut butonu ekle / düzenle."""

    EOLS = [("\\r\\n", "\r\n"), ("\\n", "\n"), ("-", "")]

    def __init__(self, parent, lang: str, cmd: dict | None = None):
        super().__init__(parent)
        t = T(lang)
        cmd = cmd or {}
        self.setWindowTitle(t("qc_title"))
        self.setMinimumWidth(420)
        form = QFormLayout(self)
        self.e_label = QLineEdit(cmd.get("label", ""))
        self.e_label.setMaxLength(24)
        self.e_cmd = QLineEdit(cmd.get("cmd", ""))
        self.e_cmd.setMaxLength(500)
        self.c_hex = QCheckBox(t("qc_hex"))
        self.c_hex.setChecked(bool(cmd.get("hex", False)))
        self.c_eol = QComboBox()
        for txt, val in self.EOLS:
            self.c_eol.addItem(t("eol_none") if val == "" else txt, val)
        i = self.c_eol.findData(cmd.get("eol", "\r\n"))
        self.c_eol.setCurrentIndex(max(0, i))
        self.s_rep = QDoubleSpinBox()
        self.s_rep.setRange(0, 3600)
        self.s_rep.setDecimals(1)
        self.s_rep.setSuffix(" s")
        self.s_rep.setValue(float(cmd.get("repeat", 0) or 0))
        self.c_hex.toggled.connect(lambda on: self.c_eol.setEnabled(not on))
        self.c_eol.setEnabled(not self.c_hex.isChecked())
        form.addRow(t("qc_label"), self.e_label)
        form.addRow(t("qc_cmd"), self.e_cmd)
        form.addRow("", self.c_hex)
        form.addRow(t("qc_eol"), self.c_eol)
        form.addRow(t("qc_repeat"), self.s_rep)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Save).setText(t("u_save"))
        bb.button(QDialogButtonBox.Save).setObjectName("Accent")
        bb.button(QDialogButtonBox.Cancel).setText(t("cancel"))
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def _ok(self) -> None:
        txt = self.e_cmd.text()
        if self.c_hex.isChecked():
            try:
                ok = bool(parse_hex(txt))
            except ValueError:
                ok = False
        else:
            ok = bool(txt.strip())
        if ok:
            self.accept()
        else:                       # boş ya da geçersiz HEX: pencere kapanmaz, kutu işaretlenir
            self.e_cmd.setStyleSheet("border: 1px solid #f85149;")
            self.e_cmd.setFocus()

    def result_cmd(self) -> dict:
        cmd = self.e_cmd.text()
        return {"label": (self.e_label.text().strip() or cmd.strip() or "?")[:24], "cmd": cmd,
                "hex": self.c_hex.isChecked(), "eol": self.c_eol.currentData() or "",
                "repeat": round(self.s_rep.value(), 1)}


class AlarmsDialog(QDialog):
    """Kanal başına eşik alarmları: koşul, eşik, geri dönüş payı, ses, karta komut."""

    COLS = ("a_col_on", "a_col_ch", "a_col_op", "a_col_value", "a_col_hyst", "a_col_beep", "a_col_cmd")

    def __init__(self, parent, lang: str, channels: list[str], alarms: list[dict], units: dict):
        super().__init__(parent)
        t = self.t = T(lang)
        self.channels = list(channels)
        self.units = units
        self.setWindowTitle(t("alarms_title"))
        self.setMinimumSize(900, 380)
        lay = QVBoxLayout(self)
        info = QLabel(t("alarms_help"))
        info.setWordWrap(True)
        info.setObjectName("Hint")
        lay.addWidget(info)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels([t(c) for c in self.COLS])
        self.table.verticalHeader().setVisible(False)
        hh = self.table.horizontalHeader()
        for c in (0, 2, 3, 4, 5):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(6, QHeaderView.Stretch)
        for a in alarms:
            self._add_row(a)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        b_add = QPushButton(t("a_add"))
        b_add.clicked.connect(lambda: self._add_row(None))
        b_del = QPushButton(t("a_del"))
        b_del.clicked.connect(self._del_rows)
        row.addWidget(b_add)
        row.addWidget(b_del)
        row.addStretch(1)
        lay.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Save).setText(t("u_save"))
        bb.button(QDialogButtonBox.Save).setObjectName("Accent")
        bb.button(QDialogButtonBox.Cancel).setText(t("cancel"))
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _check_item(self, on: bool) -> QTableWidgetItem:
        it = QTableWidgetItem()
        it.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        it.setCheckState(Qt.Checked if on else Qt.Unchecked)
        return it

    def _add_row(self, a: dict | None) -> None:
        if self.table.rowCount() >= analysis.MAX_ALARMS:
            return
        a = a or {"on": True, "ch": self.channels[0] if self.channels else "", "op": ">", "value": 0.0,
                  "hyst": 0.0, "beep": False, "cmd": ""}
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, self._check_item(a["on"]))
        ch = QComboBox()
        ch.setEditable(True)
        ch.addItems(list(dict.fromkeys(self.channels + ([a["ch"]] if a["ch"] else []))))
        ch.setCurrentText(a["ch"])
        self.table.setCellWidget(r, 1, ch)
        op = QComboBox()
        op.addItem(self.t("a_op_gt"), ">")
        op.addItem(self.t("a_op_lt"), "<")
        op.setCurrentIndex(0 if a["op"] == ">" else 1)
        self.table.setCellWidget(r, 2, op)
        self.table.setItem(r, 3, QTableWidgetItem(f"{a['value']:.10g}"))
        self.table.setItem(r, 4, QTableWidgetItem(f"{a['hyst']:.10g}"))
        self.table.setItem(r, 5, self._check_item(a["beep"]))
        self.table.setItem(r, 6, QTableWidgetItem(a["cmd"]))

    def _del_rows(self) -> None:
        for r in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(r)

    @staticmethod
    def _num(item) -> float | None:
        try:
            v = float((item.text() if item is not None else "").strip().replace(",", ".") or "nan")
        except ValueError:
            return None
        return v if math.isfinite(v) else None

    def accept(self) -> None:
        """Geçersiz satır varsa pencere kapanmaz, hatalı hücre işaretlenir (alarm sessizce silinmesin)."""
        bad_first = None
        for r in range(self.table.rowCount()):
            checks = [(3, self._num(self.table.item(r, 3)) is not None),
                      (4, (self.table.item(r, 4) is None or not self.table.item(r, 4).text().strip())
                       or self._num(self.table.item(r, 4)) is not None)]
            for c, ok in checks:
                it = self.table.item(r, c)
                if it is not None:
                    it.setBackground(QColor(0, 0, 0, 0) if ok else QColor(248, 81, 73, 90))
                if not ok and bad_first is None:
                    bad_first = (r, c)
            ch = self.table.cellWidget(r, 1)
            ok_ch = bool(ch.currentText().strip())
            ch.setStyleSheet("" if ok_ch else "border: 1px solid #f85149;")
            if not ok_ch and bad_first is None:
                bad_first = (r, 1)
        if bad_first is not None:
            self.table.setCurrentCell(*bad_first)
            return
        super().accept()

    def result_alarms(self) -> list[dict]:
        out = []
        for r in range(self.table.rowCount()):
            try:
                value = float(self.table.item(r, 3).text().replace(",", "."))
                hyst = float((self.table.item(r, 4).text().strip() or "0").replace(",", "."))
            except (ValueError, AttributeError):
                continue
            out.append({"on": self.table.item(r, 0).checkState() == Qt.Checked,
                        "ch": self.table.cellWidget(r, 1).currentText().strip(),
                        "op": self.table.cellWidget(r, 2).currentData(),
                        "value": value, "hyst": hyst,
                        "beep": self.table.item(r, 5).checkState() == Qt.Checked,
                        "cmd": self.table.item(r, 6).text().strip()})
        return analysis.sanitize_alarms(out)
