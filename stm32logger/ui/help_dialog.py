# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Mehmet Can Dusgun (mhmtcndsgn) — Oskilo
"""Uygulama içi kullanım kılavuzu penceresi ve Hakkında penceresi."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication, QImage, QPixmap, QTextDocument
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QListWidget, QPushButton, QScrollArea,
                               QTextBrowser, QVBoxLayout)

from .. import APP_NAME, REPO_URL, __version__
from ..help import body_html, topics
from ..i18n import T
from . import theme as th

HELP_IMG_SCALE = 2.0   # kılavuz görselleri 2 kat çözünürlükte üretilir (packaging/make_help_images.py)


def crisp(path: str, logical_w: int, dpr: float) -> QImage:
    """Görseli ekranın gerçek piksel sayısına yumuşak (bulanık değil, pürüzsüz) küçültür.

    Böylece Windows'ta %125-%150 ekran ölçeğinde de görsel pikselleşmez.
    """
    src = QImage(path)
    if src.isNull():
        return src
    target = max(1, int(round(logical_w * dpr)))
    img = src if target >= src.width() else src.scaledToWidth(target, Qt.SmoothTransformation)
    img.setDevicePixelRatio(img.width() / logical_w)
    return img


class _Browser(QTextBrowser):
    """Kılavuz görsellerini bellekteki net kopyalardan verir."""

    def __init__(self):
        super().__init__()
        self.images: dict[str, QImage] = {}

    def loadResource(self, kind, url):
        if kind == QTextDocument.ImageResource and url.toString() in self.images:
            return self.images[url.toString()]
        return super().loadResource(kind, url)


class ImageViewer(QDialog):
    """Kılavuzdaki bir görsele tıklanınca tam boyutunu gösterir."""

    def __init__(self, parent, path: str, title: str):
        super().__init__(parent)
        self.setWindowTitle(title)
        dpr = self.devicePixelRatioF() or 1.0
        src = QImage(path)
        logical_w = max(1, int(src.width() / HELP_IMG_SCALE))
        pm = QPixmap.fromImage(crisp(path, logical_w, dpr))
        lbl = QLabel()
        lbl.setPixmap(pm)
        lbl.setAlignment(Qt.AlignCenter)
        area = QScrollArea()
        area.setWidget(lbl)
        area.setWidgetResizable(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(area)
        scr = QGuiApplication.primaryScreen().availableGeometry() if QGuiApplication.primaryScreen() else None
        lw, lh = logical_w, int(src.height() / HELP_IMG_SCALE)
        w = min(lw + 30, int(scr.width() * 0.92)) if scr else lw + 30
        h = min(lh + 30, int(scr.height() * 0.9)) if scr else lh + 30
        self.resize(max(400, w), max(300, h))


class HelpDialog(QDialog):
    def __init__(self, parent, lang: str, help_dir: str):
        super().__init__(parent)
        self.help_dir = help_dir
        self.lang = lang
        self.setObjectName("HelpDialog")
        self.resize(1100, 760)
        self.setMinimumSize(760, 520)

        self.list = QListWidget()
        self.list.setFixedWidth(270)
        self.list.setWordWrap(True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.currentRowChanged.connect(lambda _: self._render())

        self.browser = _Browser()
        self.browser.setOpenLinks(False)
        self.browser.setSearchPaths([help_dir])
        self.browser.anchorClicked.connect(self._on_link)

        self.btn_prev = QPushButton()
        self.btn_next = QPushButton()
        self.btn_next.setObjectName("Accent")
        self.btn_web = QPushButton()
        self.btn_web.setObjectName("Link")
        self.btn_close = QPushButton()
        self.btn_prev.clicked.connect(lambda: self._step(-1))
        self.btn_next.clicked.connect(lambda: self._step(1))
        self.btn_web.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(REPO_URL)))
        self.btn_close.clicked.connect(self.close)

        right = QVBoxLayout()
        right.setSpacing(8)
        right.addWidget(self.browser, 1)
        nav = QHBoxLayout()
        nav.addWidget(self.btn_web)
        nav.addStretch(1)
        nav.addWidget(self.btn_prev)
        nav.addWidget(self.btn_next)
        nav.addWidget(self.btn_close)
        right.addLayout(nav)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(12)
        lay.addWidget(self.list)
        lay.addLayout(right, 1)

        self._resize_timer = QTimer(self, singleShot=True, interval=60, timeout=self._render)
        self.set_lang(lang)
        self.list.setCurrentRow(0)

    # ------------------------------------------------------------------
    def set_lang(self, lang: str) -> None:
        self.lang = lang
        t = T(lang)
        self.setWindowTitle(f"{APP_NAME} — {t('help_title')}")
        row = max(0, self.list.currentRow())
        self.list.blockSignals(True)
        self.list.clear()
        self._keys = []
        for i, (key, title) in enumerate(topics(lang), start=1):
            self.list.addItem(f"{i}.  {title}")
            self._keys.append(key)
        self.list.setCurrentRow(row)
        self.list.blockSignals(False)
        self.btn_prev.setText(t("help_prev"))
        self.btn_next.setText(t("help_next"))
        self.btn_close.setText(t("help_close"))
        self.btn_web.setText(t("help_web"))
        self._render()

    def show_topic(self, key: str) -> None:
        if key in getattr(self, "_keys", []):
            self.list.setCurrentRow(self._keys.index(key))

    def _step(self, d: int) -> None:
        r = self.list.currentRow() + d
        if 0 <= r < self.list.count():
            self.list.setCurrentRow(r)

    def _img_path(self, name: str) -> str:
        return os.path.join(self.help_dir, self.lang, f"{name}.png")

    def refresh_theme(self) -> None:
        self._render()

    def _render(self) -> None:
        row = self.list.currentRow()
        if row < 0 or not getattr(self, "_keys", None):
            return
        maxw = max(320, min(980, self.browser.viewport().width() - 56))
        dpr = self.devicePixelRatioF() or 1.0
        self.browser.images.clear()

        def img(name):
            path = self._img_path(name)
            size = QImage(path).size()
            if size.isEmpty():
                return ""
            w = int(min(maxw, size.width() / HELP_IMG_SCALE))     # asla büyütme
            key = f"mem:{self.lang}/{name}/{w}/{dpr:g}"
            self.browser.images[key] = crisp(path, w, dpr)
            return f'<img src="{key}" width="{w}">'

        self.browser.document().setDefaultStyleSheet(th.help_css())
        html = body_html(self.lang, self._keys[row], img, lambda n: os.path.exists(self._img_path(n)))
        y = self.browser.verticalScrollBar().value() if getattr(self, "_last_row", None) == row else 0
        self.browser.setHtml(html)
        self.browser.verticalScrollBar().setValue(y)
        self._last_row = row
        self.btn_prev.setEnabled(row > 0)
        self.btn_next.setEnabled(row < self.list.count() - 1)

    def _on_link(self, url: QUrl) -> None:
        if url.scheme() == "img":
            name = url.path() or url.toString().split(":", 1)[-1]
            p = self._img_path(name)
            if os.path.exists(p):
                ImageViewer(self, p, self.windowTitle()).exec()
        else:
            QDesktopServices.openUrl(url)

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        self._resize_timer.start()


class AboutDialog(QDialog):
    def __init__(self, parent, lang: str, icon_path: str, can_unblock: bool, on_unblock, on_updates,
                 on_verify=None):
        super().__init__(parent)
        t = T(lang)
        self.setWindowTitle(t("menu_about"))
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(12)

        top = QHBoxLayout()
        logo = QLabel()
        pm = QPixmap(icon_path)
        if not pm.isNull():
            logo.setPixmap(pm.scaled(64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        top.addWidget(logo, 0, Qt.AlignTop)
        txt = QLabel(t("about_html", name=APP_NAME, ver=__version__, url=REPO_URL))
        txt.setWordWrap(True)
        txt.setTextFormat(Qt.RichText)
        txt.setOpenExternalLinks(True)
        top.addWidget(txt, 1)
        lay.addLayout(top)

        row = QHBoxLayout()
        b_upd = QPushButton(t("menu_updates"))
        b_upd.clicked.connect(on_updates)
        row.addWidget(b_upd)
        if can_unblock:
            b_unb = QPushButton(t("about_unblock"))
            b_unb.setToolTip(t("about_unblock_tip"))
            b_unb.clicked.connect(on_unblock)
            row.addWidget(b_unb)
        if on_verify is not None:
            b_ver = QPushButton(t("verify_btn"))
            b_ver.setToolTip(t("verify_tip"))
            b_ver.clicked.connect(on_verify)
            row.addWidget(b_ver)
        row.addStretch(1)
        b_close = QPushButton(t("help_close"))
        b_close.setObjectName("Accent")
        b_close.clicked.connect(self.accept)
        row.addWidget(b_close)
        lay.addLayout(row)
