# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Mehmet Can Dusgun (mhmtcndsgn) — Oskilo
"""Güncelleme indirme penceresi: ilerleme çubuğu ve İptal.

İş arka planda (thread) çalışır; pencere durumu bir zamanlayıcıyla okur. Böylece Qt nesnelerine
sadece arayüz thread'inden dokunulur.
"""
from __future__ import annotations

import threading

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout

from ..updater import Cancelled

MB = 1024 * 1024


class UpdateDialog(QDialog):
    def __init__(self, parent, t, title: str):
        super().__init__(parent)
        self.t = t
        self.setWindowTitle(title)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.setMinimumWidth(420)
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 18, 20, 16)
        v.setSpacing(12)
        self.lbl = QLabel(t("upd_dl_start"))
        self.lbl.setWordWrap(True)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 0)
        self.bar.setFixedHeight(8)
        v.addWidget(self.lbl)
        v.addWidget(self.bar)
        row = QHBoxLayout()
        row.addStretch(1)
        self.btn_cancel = QPushButton(t("cancel"))
        self.btn_cancel.clicked.connect(self.reject)
        row.addWidget(self.btn_cancel)
        v.addLayout(row)
        self._state = (0, 0, "dl")
        self._cancel = False
        self._result = None          # ("ok", değer) | ("err", hata) | ("cancel", None)
        self._timer = QTimer(self, interval=100, timeout=self._poll)

    # ------------------------------------------------------------ dış arayüz
    def run(self, fn) -> tuple:
        """fn(progress, cancel) arka planda çalışır. Döndürür: ("ok", sonuç) / ("err", hata) / ("cancel", None)."""

        def progress(done: int, total: int, phase: str) -> None:
            self._state = (done, total, phase)

        def job() -> None:
            try:
                res = ("ok", fn(progress, lambda: self._cancel))
            except Cancelled:
                res = ("cancel", None)
            except Exception as e:  # noqa: BLE001
                res = ("err", e)
            self._result = res

        self._thread = threading.Thread(target=job, name="update", daemon=True)
        self._thread.start()
        self._timer.start()
        self.exec()
        self._timer.stop()
        if self._result is None:                 # kullanıcı İptal'e bastı: iş bitene kadar bekle
            self._cancel = True
            self._thread.join(30)
        res = self._result or ("cancel", None)
        if self._cancel and res[0] == "ok":      # iptalden sonra bitti: iptal sayılır (arta kalanı çağıran siler)
            return ("cancel", res[1])
        return res

    # ------------------------------------------------------------ iç işleyiş
    def reject(self) -> None:
        self._cancel = True
        self.btn_cancel.setEnabled(False)
        self.lbl.setText(self.t("upd_cancelling"))
        if self._result is not None:
            super().reject()

    def _poll(self) -> None:
        if self._result is not None:
            self._timer.stop()
            self.accept() if self._result[0] == "ok" else super().reject()
            return
        if self._cancel:
            return
        done, total, phase = self._state
        t = self.t
        if phase == "dl":
            if total > 0:
                self.bar.setRange(0, 1000)
                self.bar.setValue(int(1000 * min(done, total) / total))
                self.lbl.setText(t("upd_dl", done=f"{done / MB:.1f}", total=f"{total / MB:.1f}"))
            else:
                self.bar.setRange(0, 0)
                self.lbl.setText(t("upd_dl_start"))
        else:
            self.bar.setRange(0, 0)
            self.lbl.setText(t("upd_verify") if phase == "verify" else t("upd_extract"))
