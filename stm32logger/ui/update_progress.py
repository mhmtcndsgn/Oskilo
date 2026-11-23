# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Mehmet Can Dusgun (mhmtcndsgn) — Oskilo
"""Güncelleme yardımcısının küçük ilerleme penceresi.

Eski Oskilo kapandıktan yeni sürümün açılış ekranı görünene kadar ekranda bir şey olsun diye
gösterilir ("Oskilo güncelleniyor… %60"). Sadece QtCore/QtGui/QtWidgets kullanır; numpy ve
pyqtgraph yüklenmez, böylece birkaç yüz ms içinde açılır. Bir hata olursa updater pencere olmadan
devam eder.
"""
from __future__ import annotations

import time

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from .. import APP_NAME
from ..i18n import T
from . import theme as th
from .splash import ASSETS, UI_FONTS, _font, splash_theme

W, H = 420, 136


class _Window(QWidget):
    def __init__(self, t, colors: dict):
        super().__init__(None, Qt.SplashScreen | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle(APP_NAME)
        self.setFixedSize(W, H)
        self.t = t
        self.c = colors
        self.text = ""
        self.frac = 0.0
        logo = ASSETS / "icon.png"
        self.logo = QPixmap(str(logo)) if logo.exists() else QPixmap()
        scr = QGuiApplication.primaryScreen()
        if scr is not None:
            g = scr.availableGeometry()
            self.move(g.center().x() - W // 2, g.center().y() - H // 2)

    def paintEvent(self, _ev) -> None:  # noqa: N802
        c = self.c
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)
        r = QRectF(0.5, 0.5, W - 1, H - 1)
        path = QPainterPath()
        path.addRoundedRect(r, 12, 12)
        p.setClipPath(path)
        bg = QColor(c["BG_PANEL"]) if c["MODE"] == "light" else QColor(c["BG"])
        p.fillRect(r, bg)
        if not self.logo.isNull():
            p.drawPixmap(QRectF(24, 22, 30, 30), self.logo, QRectF(self.logo.rect()))
        p.setPen(QColor(c["TEXT"]))
        p.setFont(_font(UI_FONTS, 12.5, QFont.DemiBold))
        p.drawText(QRectF(66, 20, W - 90, 22), Qt.AlignLeft | Qt.AlignVCenter, self.t("upd_p_title"))
        p.setPen(QColor(c["MUTED"]))
        p.setFont(_font(UI_FONTS, 8.6))
        p.drawText(QRectF(66, 40, W - 90, 18), Qt.AlignLeft | Qt.AlignVCenter, self.t("upd_p_note"))
        p.setPen(QColor(c["TEXT"]))
        p.setFont(_font(UI_FONTS, 9.2))
        p.drawText(QRectF(24, 70, W - 90, 20), Qt.AlignLeft | Qt.AlignVCenter, self.text)
        p.setPen(QColor(c["MUTED"]))
        pct = self.t("upd_p_pct", p=round(self.frac * 100))
        p.drawText(QRectF(W - 74, 70, 50, 20), Qt.AlignRight | Qt.AlignVCenter, pct)
        bar = QRectF(24, 100, W - 48, 6)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(c["BORDER_SOFT"]))
        p.drawRoundedRect(bar, 3, 3)
        if self.frac > 0:
            p.setBrush(QColor(c["ACCENT_TXT"]))
            p.drawRoundedRect(QRectF(bar.x(), bar.y(), bar.width() * min(1.0, self.frac), bar.height()), 3, 3)
        p.setClipping(False)
        p.setPen(QPen(QColor(c["BORDER"]), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r, 12, 12)
        p.end()


class ProgressUI:
    """updater._UI arayüzü: status(key, frac, **kw), tick(), close()."""

    def __init__(self):
        from ..system import app_settings

        self.app = QApplication.instance() or QApplication([APP_NAME])
        settings = app_settings()
        lang = str(settings.value("lang", "tr"))
        self.t = T(lang if lang in ("tr", "en") else "tr")
        mode, accent = splash_theme(settings)
        th.apply(mode, accent)
        colors = {k: getattr(th, k) for k in ("BG", "BG_PANEL", "BORDER", "BORDER_SOFT", "TEXT", "MUTED",
                                              "ACCENT_TXT", "MODE")}
        self.win = _Window(self.t, colors)
        self.win.text = self.t("upd_p_wait")
        self.win.show()
        self.win.raise_()
        self._last = 0.0
        self._closed = False
        self.app.processEvents()

    def status(self, key: str, frac: float | None = None, **kw) -> None:
        if self._closed:
            return
        try:
            self.win.text = self.t(key, **kw)
        except (KeyError, IndexError, ValueError):
            self.win.text = self.t(key)
        if frac is not None:
            self.win.frac = max(self.win.frac, float(frac))
        self.win.update()
        self.tick(force=True)

    def tick(self, force: bool = False) -> None:
        if self._closed:
            return
        now = time.monotonic()
        if force or now - self._last >= 0.03:
            self._last = now
            self.app.processEvents()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.win.close()
            self.app.processEvents()
        except RuntimeError:
            pass
