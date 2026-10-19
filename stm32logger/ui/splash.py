"""Açılış ekranı: "Veri hattı" (Kart → Seri port → Doğrulama → Canlı grafik → Kayıt).

Ağır modüller (numpy, pyqtgraph, ana pencere) yüklenmeden ÖNCE gösterilir; bu yüzden burada sadece
QtCore/QtGui/QtWidgets ve hafif uygulama modülleri kullanılır. Aşamalar gerçek yükleme adımlarına
bağlıdır (main.py her adımdan önce stage() çağırır): ilerleme gerçekten ne yüklendiğini gösterir.
Python yükleme sırasında birkaç yüz ms meşgul kalabilir; bu yüzden tasarım sürekli harekete değil
adım adım yanan aşamalara dayanır, donma göze batmaz.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEasingCurve, QPointF, QPropertyAnimation, QRectF, Qt, QTimer
from PySide6.QtGui import (QColor, QFont, QFontMetricsF, QGuiApplication, QPainter, QPainterPath, QPen, QPixmap,
                           QRadialGradient)
from PySide6.QtWidgets import QApplication, QWidget

from ..i18n import T
from . import theme as th

W, H = 560, 320
ASSETS = Path(__file__).resolve().parent.parent.parent / "assets"
UI_FONTS = ["Segoe UI", "Inter", "SF Pro Text", "Helvetica Neue", "Arial"]
MONO_FONTS = ["Cascadia Mono", "Consolas", "SF Mono", "Menlo", "DejaVu Sans Mono", "Courier New"]

# aşama simgeleri (24×24, çizgi) — kılavuzdaki/web önizlemesindeki ile aynı
_ICONS = {
    "chip": '<rect x="7" y="7" width="10" height="10" rx="1.5"/><path d="M10 3v4M14 3v4M10 17v4M14 17v4M3 10h4M3 14h4M17 10h4M17 14h4"/>',
    "plug": '<path d="M9 2v5M15 2v5M7 7h10v4a5 5 0 0 1-10 0zM12 16v6"/>',
    "shield": '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/><path d="M8.5 12l2.5 2.5 4.5-5"/>',
    "chart": '<path d="M3 3v18h18"/><path d="M6.5 15l4-5 3.5 3 5.5-7"/>',
    "file": '<path d="M14 3H6.5A1.5 1.5 0 0 0 5 4.5v15A1.5 1.5 0 0 0 6.5 21h11a1.5 1.5 0 0 0 1.5-1.5V8z"/>'
            '<path d="M14 3v5h5M8.5 13h7M8.5 17h4.5"/>',
}
STAGES = (("chip", "sp_board"), ("plug", "sp_port"), ("shield", "sp_check"), ("chart", "sp_chart"), ("file", "sp_save"))
# her aşama bitince ilerleme çizgisinin varacağı yer (grafik motoru ve arayüz en uzun süren adımlar)
WEIGHTS = (0.06, 0.16, 0.26, 0.62, 0.92)


def _icon(name: str, color: str, px: int, dpr: float) -> QPixmap:
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="{round(px * dpr)}" '
           f'height="{round(px * dpr)}" fill="none" stroke="{color}" stroke-width="1.6" stroke-linecap="round" '
           f'stroke-linejoin="round">{_ICONS[name]}</svg>')
    pm = QPixmap()
    pm.loadFromData(svg.encode(), "SVG")
    pm.setDevicePixelRatio(dpr)
    return pm


def _font(families, size: float, weight=QFont.Normal, spacing: float = 0.0) -> QFont:
    f = QFont()
    f.setFamilies(families)
    f.setPointSizeF(size)
    f.setWeight(weight)
    if spacing:
        f.setLetterSpacing(QFont.AbsoluteSpacing, spacing)
    f.setHintingPreference(QFont.PreferNoHinting)
    return f


class Splash(QWidget):
    def __init__(self, lang: str, version: str, mode: str = "dark", accent: str = th.DEFAULT_ACCENT,
                 text: bool = True):
        super().__init__(None, Qt.SplashScreen | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(W, H)
        self.t = T(lang)
        self.version = version
        self.text = text                       # False: yazısız ilk kare (dile bağlı olmayan sabit resim için)
        th.apply(mode, accent)
        self.c = {k: getattr(th, k) for k in ("BG", "BG_PANEL", "BG_RAISED", "BORDER", "BORDER_SOFT", "TEXT",
                                              "MUTED", "FAINT", "ACCENT_TXT", "MODE")}
        self.lit = 0                           # yanan aşama sayısı
        self.status = self.t("sp_s0")
        self.target = self.shown = 0.0         # ilerleme (0..1): hedef ve ekranda çizilen
        self._icons: dict = {}
        logo = ASSETS / "icon.png"
        self.logo = QPixmap(str(logo)) if logo.exists() else QPixmap()
        self.anim = QTimer(self, interval=16, timeout=self._step_anim)
        scr = QGuiApplication.primaryScreen()
        if scr is not None:
            g = scr.availableGeometry()
            self.move(g.center().x() - W // 2, g.center().y() - H // 2)

    # ------------------------------------------------------------ dış arayüz
    def stage(self, i: int, status_key: str | None = None) -> None:
        """i. aşamaya geçildi (0..4): o aşama yanar, ilerleme oraya doğru gider."""
        self.lit = max(self.lit, min(len(STAGES), i + 1))
        self.target = max(self.target, WEIGHTS[min(i, len(WEIGHTS) - 1)])
        if status_key:
            self.status = self.t(status_key)
        if not self.anim.isActive():
            self.anim.start()
        self.repaint()
        QApplication.processEvents()

    def finish(self, win) -> None:
        """Ana pencere hazır: ilerleme tamamlanır, açılış ekranı yumuşakça kaybolur."""
        self.lit, self.target, self.shown = len(STAGES), 1.0, 1.0
        self.status = self.t("sp_ready")
        self.repaint()
        self.anim.stop()
        fade = QPropertyAnimation(self, b"windowOpacity", self)
        fade.setDuration(220)
        fade.setStartValue(1.0)
        fade.setEndValue(0.0)
        fade.setEasingCurve(QEasingCurve.InCubic)
        fade.finished.connect(self._done)
        QTimer.singleShot(120, fade.start)      # ana pencere ekrana çizilsin, sonra kaybol
        self._fade = fade
        if win is not None:
            win.raise_()
            win.activateWindow()

    def _done(self) -> None:
        self.close()
        self.deleteLater()

    def _step_anim(self) -> None:
        d = self.target - self.shown
        if abs(d) < 0.002:
            self.shown = self.target
            self.anim.stop()
        else:
            self.shown += d * 0.18
        self.update()

    # ------------------------------------------------------------ çizim
    def _ic(self, name: str, color: str) -> QPixmap:
        key = (name, color, self.devicePixelRatioF())
        if key not in self._icons:
            self._icons[key] = _icon(name, color, 22, self.devicePixelRatioF())
        return self._icons[key]

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
        glow = QColor(c["ACCENT_TXT"])
        glow.setAlphaF(0.07 if c["MODE"] == "dark" else 0.05)
        g = QRadialGradient(QPointF(W * 0.85, -20), W * 0.75)
        g.setColorAt(0, glow)
        g.setColorAt(1, QColor(0, 0, 0, 0))
        p.fillRect(r, g)

        # başlık: logo + Oskilo + alt yazı, sağda sürüm
        if not self.logo.isNull():
            p.drawPixmap(QRectF(32, 24, 34, 34), self.logo, QRectF(self.logo.rect()))
        p.setPen(QColor(c["TEXT"]))
        p.setFont(_font(UI_FONTS, 17, QFont.DemiBold, -0.4))
        p.drawText(QPointF(76, 46), "Oskilo")
        if self.text:
            p.setPen(QColor(c["MUTED"]))
            p.setFont(_font(UI_FONTS, 6.6, QFont.DemiBold, 2.0))
            p.drawText(QPointF(77, 62), self.t("title_b"))
        p.setPen(QColor(c["FAINT"]))
        mono_small = _font(MONO_FONTS, 7.6)
        p.setFont(mono_small)
        ver = f"v{self.version}"
        p.drawText(QPointF(W - 32 - QFontMetricsF(mono_small).horizontalAdvance(ver), 40), ver)

        # veri hattı
        n = len(STAGES)
        xs = [W * (0.14 + 0.18 * i) for i in range(n)]
        y, rad = 138.0, 24.0
        acc = QColor(c["ACCENT_TXT"])
        p.setPen(QPen(QColor(c["BORDER_SOFT"]), 2))
        p.drawLine(QPointF(xs[0], y), QPointF(xs[-1], y))
        filled = xs[max(0, min(n - 1, self.lit - 1))]          # hat, yanan son aşamaya kadar dolu
        if self.lit:
            p.setPen(QPen(acc, 2))
            p.drawLine(QPointF(xs[0], y), QPointF(filled, y))
        label_f = _font(UI_FONTS, 8.6, QFont.DemiBold)
        sub_f = _font(MONO_FONTS, 6.2)
        for i, (icon, key) in enumerate(STAGES):
            on = i < self.lit
            cur = on and (i == self.lit - 1) and self.shown < 0.999
            x = xs[i]
            if on:
                halo = QColor(acc)
                halo.setAlphaF(0.16 if cur else 0.10)
                p.setPen(Qt.NoPen)
                p.setBrush(halo)
                p.drawEllipse(QPointF(x, y), rad + (7 if cur else 4), rad + (7 if cur else 4))
            p.setBrush(bg)
            p.setPen(QPen(acc if on else QColor(c["BORDER"]), 1.6))
            p.drawEllipse(QPointF(x, y), rad, rad)
            pm = self._ic(icon, (c["ACCENT_TXT"] if on else c["FAINT"]))
            p.drawPixmap(QRectF(x - 11, y - 11, 22, 22), pm, QRectF(pm.rect()))
            if self.text:
                p.setPen(QColor(c["TEXT"] if on else c["FAINT"]))
                p.setFont(label_f)
                lab = self.t(key)
                fm = QFontMetricsF(label_f)
                p.drawText(QPointF(x - fm.horizontalAdvance(lab) / 2, y + rad + 22), lab)
                p.setPen(QColor(c["MUTED"] if on else c["FAINT"]))
                p.setFont(sub_f)
                sub = self.t(key + "_sub")
                fm2 = QFontMetricsF(sub_f)
                p.drawText(QPointF(x - fm2.horizontalAdvance(sub) / 2, y + rad + 37), sub)

        if self.text:
            p.setPen(QColor(c["MUTED"]))
            p.setFont(_font(UI_FONTS, 6.6, QFont.DemiBold, 1.8))
            p.drawText(QPointF(32, 256), self.t("sp_tag"))
            p.setFont(_font(MONO_FONTS, 7.8))
            p.setPen(QColor(c["MUTED"]))
            p.drawText(QPointF(32, 290), self.status)
            p.setPen(QColor(c["FAINT"]))
            p.setFont(mono_small)
            cr = "© 2026 M. C. Dusgun"
            p.drawText(QPointF(W - 32 - QFontMetricsF(mono_small).horizontalAdvance(cr), 290), cr)

        # pencerenin altında ince ilerleme çizgisi
        p.fillRect(QRectF(0, H - 2.5, W, 2.5), QColor(c["BORDER_SOFT"]))
        if self.shown > 0:
            p.fillRect(QRectF(0, H - 2.5, W * self.shown, 2.5), acc)
        p.setClipping(False)
        p.setPen(QPen(QColor(c["BORDER"]), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r, 12, 12)
        p.end()


def splash_theme(settings) -> tuple[str, str]:
    """Ayarlardaki tema (koyu/açık/sistem) ve renk; bozuksa varsayılan."""
    try:
        mode = str(settings.value("theme_mode", "system"))
        accent = str(settings.value("accent", th.DEFAULT_ACCENT))
    except Exception:  # noqa: BLE001
        mode, accent = "system", th.DEFAULT_ACCENT
    if mode not in th.MODES:
        mode = "system"
    if accent not in th.ACCENTS:
        accent = th.DEFAULT_ACCENT
    return th.resolve_mode(mode), accent
