"""'Kart bulunamıyor mu?' penceresi: olası sebepler ve Windows güvenlik duvarı düğmesi."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QLayout, QPushButton,
                               QVBoxLayout)

from .. import netport, winfirewall


W = 500          # yazıların genişliği (px)


class WrapLabel(QLabel):
    """Satırları kaydırılan etiket; yüksekliği metne göre ayarlanır (pencerede yazılar üst üste binmesin)."""

    def __init__(self, text=""):
        super().__init__()
        self.setWordWrap(True)
        self.setTextFormat(Qt.PlainText)
        self.setFixedWidth(W)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802
        super().setText(text)
        self.setFixedHeight(max(self.fontMetrics().height(), self.heightForWidth(W)))

    def changeEvent(self, ev) -> None:  # noqa: N802  stil (yazı tipi) değişince yüksekliği yenile
        super().changeEvent(ev)
        self.setFixedHeight(max(self.fontMetrics().height(), self.heightForWidth(W)))


class NetDiagDialog(QDialog):
    def __init__(self, parent, t, discovery):
        super().__init__(parent)
        self.t = t
        self.disc = discovery
        self.setWindowTitle(t("diag_title"))
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.setFixedWidth(W + 40)
        v = QVBoxLayout(self)
        v.setSizeConstraint(QLayout.SetFixedSize)
        v.setContentsMargins(20, 18, 20, 16)
        v.setSpacing(10)

        def label(text="", hint=False) -> QLabel:
            lbl = WrapLabel(text)
            if hint:
                lbl.setObjectName("Hint")
            v.addWidget(lbl)
            return lbl

        head = label(t("diag_head"))
        head.setStyleSheet("font-weight: 600;")
        self.lbl_found = label()
        self.lbl_ips = label(hint=True)
        self.lbl_apipa = label()
        for line in t("diag_steps").split("\n"):        # her madde ayrı satır: uzun yazı kesilmesin
            label(line)

        if winfirewall.supported():
            line = QFrame()
            line.setFrameShape(QFrame.HLine)
            line.setObjectName("Sep")
            v.addWidget(line)
            fh = label(t("fw_head"))
            fh.setStyleSheet("font-weight: 600;")
            label(t("fw_text"), hint=True)
            row = QHBoxLayout()
            self.btn_fw = QPushButton(t("fw_btn"))
            self.btn_fw.clicked.connect(self._allow)
            row.addWidget(self.btn_fw)
            row.addStretch(1)
            v.addLayout(row)
            self.lbl_fw = label()
            self.lbl_fw.hide()
            QTimer.singleShot(50, self._check_rule)

        row = QHBoxLayout()
        row.addStretch(1)
        self.btn_rescan = QPushButton(t("diag_rescan"))
        self.btn_rescan.clicked.connect(self._rescan)
        btn_close = QPushButton("OK")
        btn_close.setObjectName("Primary")
        btn_close.clicked.connect(self.accept)
        row.addWidget(self.btn_rescan)
        row.addWidget(btn_close)
        v.addLayout(row)
        self._timer = QTimer(self, interval=1000, timeout=self._refresh)
        self._timer.start()
        self._refresh()

    def _refresh(self) -> None:
        t = self.t
        ips = netport.local_ipv4()
        self.lbl_found.setText(t("diag_found", n=len(self.disc.devices())))
        self.lbl_ips.setText(t("diag_ips", ips=", ".join(ips)) if ips else t("diag_ips_none"))
        apipa = any(ip.startswith("169.254.") for ip in ips)
        self.lbl_apipa.setText(t("diag_apipa_ok") if apipa else t("diag_apipa_no"))

    def _rescan(self) -> None:
        self.disc.query_now()
        QTimer.singleShot(800, self._refresh)

    def _show_fw(self, text: str) -> None:
        self.lbl_fw.setText(text)
        self.lbl_fw.show()

    def _check_rule(self) -> None:
        if winfirewall.rule_exists():
            self._show_fw(self.t("fw_exists"))

    def _allow(self) -> None:
        t = self.t
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            res, detail = winfirewall.allow_program()
        finally:
            QApplication.restoreOverrideCursor()
        text = {"ok": t("fw_ok"), "cancel": t("fw_cancel"), "notwin": t("fw_notwin")}.get(
            res, t("fw_fail", err=detail))
        self._show_fw(text)
        if res == "ok":
            self.disc.query_now()
