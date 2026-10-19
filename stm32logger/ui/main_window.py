"""Ana pencere."""
from __future__ import annotations

import collections
import datetime as _dt
import html
import json
import math
import os
import queue
import shutil
import sys
import threading
import weakref
import time
from bisect import bisect_left, bisect_right
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QEvent, QEventLoop, QObject, QRectF, QStandardPaths, Qt, QTimer, QUrl
from PySide6.QtGui import (QAction, QActionGroup, QColor, QDesktopServices, QFont, QFontDatabase, QGuiApplication,
                           QIcon, QIntValidator, QKeySequence, QPainter, QPixmap, QTextCharFormat, QTextCursor)
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFrame,
                               QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget,
                               QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QProgressDialog,
                               QPushButton, QScrollArea, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget,
                               QToolButton, QVBoxLayout, QWidget)

from .. import APP_NAME, __version__
from .. import analysis, system
from ..engine import DEMO_PORT, Engine, list_ports, open_port
from ..exporter import FORMATS, default_filename, export_session
from ..i18n import T
from ..session import Session, clean_note, extract as extract_session
from . import theme as th
from ..analysis import parse_hex, sanitize_cmds
from .tools import AlarmsDialog, QuickCmdDialog, UnitsDialog
from .widgets import Card, PanelHeader, Segmented, Tile, guard_wheel, hint_label, hrow

ASSETS = Path(__file__).resolve().parent.parent.parent / "assets"
BAUDS = ["9600", "19200", "38400", "57600", "115200", "230400", "460800", "921600", "1000000", "2000000"]
WINDOWS = [10, 30, 60, 300, 0]  # sn, 0 = tümü
TERM_MAX_PER_TICK = 400
HEX_MAX_PER_TICK = 4096      # HEX görünümde bir seferde en fazla bu kadar bayt gösterilir
HISTORY_MAX = 30
DISK_LOW_MB = 500
AUTOSAVE_MIN = [0, 1, 5, 10, 30]   # canlı izlemede otomatik kaydetme aralığı (dk), 0 = kapalı


def _tip(text: str) -> str:
    """Araç ipucu: karttan/USB'den gelen metin HTML olarak yorumlanmasın."""
    return "<p>" + html.escape(str(text)) + "</p>"


def _swatch(color: str, size: int = 16) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawEllipse(1, 1, size - 2, size - 2)
    p.end()
    return QIcon(pm)


def _hex_lines(data: bytes) -> list[str]:
    out = []
    for i in range(0, len(data), 16):
        chunk = data[i:i + 16]
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        out.append(f"{chunk.hex(' ').upper():<47}  |{asc}|")
    return out


def _fmt_dur(sec: float) -> str:
    sec = max(0, int(sec))
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _fmt_int(n: int) -> str:
    return f"{n:,}".replace(",", ".")


ALARM_NOTE_MAX = 300                 # bir kayda en fazla bu kadar alarm notu düşülür


class _TitleBars(QObject):
    """Windows'ta açılan her pencerenin (ana pencere, uyarılar, diyaloglar) başlık çubuğunu
    tema rengine boyar."""

    def eventFilter(self, obj, ev) -> bool:
        if ev.type() == QEvent.Show and obj.isWidgetType() and obj.isWindow():
            style_title_bar(obj)
        return False


def style_title_bar(w) -> None:
    if sys.platform != "win32":
        return
    kind = w.windowFlags() & Qt.WindowType_Mask
    if kind not in (Qt.Window, Qt.Dialog):
        return
    try:
        system.set_title_bar(int(w.winId()), th.BG, th.TEXT, th.BORDER, th.MODE == "dark")
    except Exception:  # noqa: BLE001
        pass


def status_icon(kind: str, size: int = 44) -> QPixmap:
    """Uyarı pencereleri için tema renginde simge (Windows'un mavi 'i' simgesi yerine)."""
    color, glyph = {"error": (th.RED_HI, "×"), "warn": ("#d29922", "!"),
                    "question": (th.ACCENT, "?")}.get(kind, (th.ACCENT, "i"))
    dpr = QGuiApplication.primaryScreen().devicePixelRatio() if QGuiApplication.primaryScreen() else 1.0
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawEllipse(QRectF(1, 1, size - 2, size - 2))
    f = QFont()
    f.setBold(True)
    f.setPixelSize(int(size * (0.62 if glyph == "×" else 0.56)))
    p.setFont(f)
    p.setPen(QColor("#ffffff"))
    p.drawText(QRectF(0, -1, size, size), Qt.AlignCenter, glyph)
    p.end()
    return pm


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = system.app_settings()
        if sys.platform == "win32":          # başlık çubukları tema renginde (Windows 11; Windows 10'da koyu/açık)
            self._title_bars = _TitleBars(self)
            QApplication.instance().installEventFilter(self._title_bars)
        self.lang = self._sv("lang", "tr")
        if self.lang not in ("tr", "en"):
            self.lang = "tr"
        self.t = T(self.lang)
        try:
            self._history = [str(x) for x in json.loads(self._sv("history", "[]"))][-HISTORY_MAX:]
        except (ValueError, TypeError):
            self._history = []
        self._hist_pos = len(self._history)

        self.engine: Engine | None = None
        self.view: Session | None = None          # ekranda gösterilen oturum
        self.banner_session: Session | None = None
        self.last_saved: str | None = None
        self._export_q: queue.SimpleQueue = queue.SimpleQueue()
        self._export_threads: list[threading.Thread] = []
        self._reserved: set[str] = set()   # yazılmakta olan dosyalar (aynı ada iki kayıt olmasın)
        self._unsaved: list[Session] = []   # biten ama kaydedilmemiş kayıtlar (kapatırken sorulur)
        self._rate = collections.deque()
        self._conn_t0 = 0.0
        self._conn_info = ("", 0, "live")
        self._curves: dict[str, pg.PlotDataItem] = {}
        self._gap_items: list = []
        self._plot_t = 0.0
        self._plot_dur = 0.0
        self._curve_owner = None
        self._plot_sig = None
        self._port_sig = None
        self._last_plot = None          # analiz için son çizilen veri (x, birimli y'ler, adlar)
        self._cursor_lines: list = []
        self._note_items: dict = {}
        self._plot_collapsed = False
        self._cursor_ctx = None
        self._note_owner = None
        self._analysis_dur = 0.0
        self._qc_fail: dict[int, int] = {}
        self._notes_sig = None
        self._fft_curves: dict[str, pg.PlotDataItem] = {}
        self._peaks: dict[str, float] = {}
        self._analysis_t = 0.0
        self._fft_fresh = True          # FFT x eksenini bir kez anlamlı aralığa ayarla
        try:
            self._units = analysis.sanitize_units(json.loads(self._sv("units", "{}")))
        except ValueError:
            self._units = {}
        try:
            self._qcmds = sanitize_cmds(json.loads(self._sv("quick_cmds", "")))
        except ValueError:
            self._qcmds = []
        if self._sv("quick_cmds", "") == "":
            word = "GONDER" if self.lang == "tr" else "SEND"     # DEMO ve örnek kart kodu ikisini de anlar
            self._qcmds = [{"label": word, "cmd": word, "hex": False, "eol": "\r\n", "repeat": 0.0}]
        self._qc_timers: dict[int, QTimer] = {}
        try:
            self._alarms = analysis.sanitize_alarms(json.loads(self._sv("alarms", "[]")))
        except ValueError:
            self._alarms = []
        self._alarm_armed: dict[int, bool] = {}
        self._alarm_scanned = weakref.WeakKeyDictionary()   # oturum -> alarm için kontrol edilen satır sayısı
        self._alarm_notes = weakref.WeakKeyDictionary()     # oturum -> eklenen alarm notu sayısı
        self._alarm_pending: dict = {}       # (alarm, oturum) -> [sayı, etiket, son değer]: saniye sınırında bekleyen
        self._alarm_fired_t: dict[int, tuple] = {}     # alarm -> (zaman, oturum ref)
        self._alarm_lines: list = []
        self._alarm_sig = None
        # tetikli kayıt: _trig = sürmekte olan olay (parçalar, kaynak oturum, bitiş zamanı...) ya da None
        self._trig: dict | None = None
        self._trig_tail: Session | None = None     # bölünen arabelleğin son 'önce' saniyesi (sınırda tetik için)
        self._trig_count = 0
        self._conn_trig = False                    # bu bağlantı tetikli kayıt modunda mı
        self._trig_pre_s, self._trig_dur_s = 5, 60

        self._theme_mode = self._sv("theme_mode", "system")
        if self._theme_mode not in th.MODES:
            self._theme_mode = "system"
        self._accent = self._sv("accent", th.DEFAULT_ACCENT)
        if self._accent not in th.ACCENTS:
            self._accent = th.DEFAULT_ACCENT
        th.apply(th.resolve_mode(self._theme_mode), self._accent)
        QApplication.instance().setStyleSheet(th.build_qss(ASSETS))
        for w in QApplication.topLevelWidgets():       # açık pencerelerin başlık çubukları da
            if w.isVisible():
                style_title_bar(w)

        self.setWindowTitle(APP_NAME)
        icon = ASSETS / "icon.png"
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))
        self.resize(1360, 860)
        self.setMinimumSize(960, 620)

        self._build()
        guard_wheel(self.port_cb, self.baud_cb, self.fs_spin, self.seq_cb, self.idle_spin, self.trig_pre, self.trig_dur,
                    self.x_cb, self.win_cb, self.eol_cb, self.lang_cb)
        self._load_settings()
        self.retranslate()
        self._refresh_ports(initial=True)
        self._set_controls_enabled(True)
        self._on_mode_changed(self.seg_mode.value())
        self._update_stats()

        self.ui_timer = QTimer(self, interval=50, timeout=self._tick)
        self.ui_timer.start()
        self.plot_timer = QTimer(self, interval=200, timeout=self._update_plot)
        self.plot_timer.start()
        self.port_timer = QTimer(self, interval=2000, timeout=lambda: self._refresh_ports(silent=True))
        self.port_timer.start()
        self._last_autosave = self._last_disk = self._last_retry = time.monotonic()
        self._disk_low = False
        self.long_timer = QTimer(self, interval=15000, timeout=self._periodic)
        self.long_timer.start()
        self.alarm_timer = QTimer(self, interval=100, timeout=self._check_alarms)
        self.alarm_timer.start()

        self._help_dlg = None
        self._bg_q: queue.SimpleQueue = queue.SimpleQueue()
        self.addAction(self.act_guide)   # F1 menü kapalıyken de çalışsın
        try:   # Windows'ta koyu/açık mod değişince "Sistem" teması da değişsin
            QGuiApplication.styleHints().colorSchemeChanged.connect(
                lambda *_: self._theme_mode == "system" and self._apply_theme(save=False))
        except (AttributeError, TypeError):
            pass
        for keys, fn in ((("Ctrl+S",), self._save_now), (("Ctrl+Shift+S",), self._save_as),
                         (("Ctrl+O",), self._open_saved_file),
                         (("F5",), self._toggle_connect), (("Ctrl+L",), self.term.clear),
                         (("Ctrl+M",), self._add_note)):
            act = QAction(self, triggered=fn)
            act.setShortcuts([QKeySequence(k) for k in keys])
            act.setShortcutContext(Qt.WindowShortcut)
            self.addAction(act)
        if not any(f in sys.argv for f in ("--selftest", "--no-first-run")):
            QTimer.singleShot(500, self._first_run)

    # ================================================================ ARAYÜZ
    def _build(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        v = QVBoxLayout(root)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self._build_header())

        body = QHBoxLayout()
        body.setContentsMargins(16, 14, 16, 12)
        body.setSpacing(14)
        v.addLayout(body, 1)
        body.addWidget(self._build_sidebar())
        body.addWidget(self._build_main(), 1)
        self._build_statusbar()

    def _build_header(self) -> QWidget:
        hdr = QFrame()
        hdr.setObjectName("Header")
        lay = QHBoxLayout(hdr)
        lay.setContentsMargins(20, 10, 20, 10)
        lay.setSpacing(10)
        logo = QLabel()
        pm = QPixmap(str(ASSETS / "icon.png"))
        if not pm.isNull():
            logo.setPixmap(pm.scaled(34, 34, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        logo.setStyleSheet("background: transparent;")
        lay.addWidget(logo)
        self.lbl_title_a = QLabel()
        self.lbl_title_a.setObjectName("TitleMain")
        self.lbl_title = QLabel()
        self.lbl_title.setObjectName("Tagline")
        lay.addWidget(self.lbl_title_a)
        lay.addWidget(self.lbl_title)
        lay.addStretch(1)
        self.lbl_lang = QLabel()
        self.lbl_lang.setObjectName("Version")
        self.lang_cb = QComboBox()
        self.lang_cb.addItem("Türkçe", "tr")
        self.lang_cb.addItem("English", "en")
        self.lang_cb.setCurrentIndex(0 if self.lang == "tr" else 1)
        self.lang_cb.setFixedWidth(110)
        self.lang_cb.currentIndexChanged.connect(lambda _: self._set_lang(self.lang_cb.currentData()))
        ver = QLabel(f"v{__version__}")
        ver.setObjectName("Version")
        from PySide6.QtWidgets import QMenu, QToolButton

        self.btn_help = QToolButton()
        self.btn_help.setObjectName("HelpBtn")
        self.btn_help.setText("?")
        self.btn_help.setCursor(Qt.PointingHandCursor)
        self.btn_help.setPopupMode(QToolButton.InstantPopup)
        self.help_menu = QMenu(self.btn_help)
        self.act_guide = QAction(self.help_menu, triggered=lambda: self.show_help())
        self.act_guide.setShortcut(QKeySequence("F1"))
        self.act_updates = QAction(self.help_menu, triggered=self._check_updates)
        self.act_report = QAction(self.help_menu, triggered=self._report_issue)
        self.act_about = QAction(self.help_menu, triggered=self._show_about)
        self.act_reset = QAction(self.help_menu, triggered=self._reset_settings)
        self.help_menu.addAction(self.act_guide)
        self.help_menu.addSeparator()
        self.help_menu.addAction(self.act_updates)
        self.help_menu.addAction(self.act_report)
        self.help_menu.addAction(self.act_reset)
        self.help_menu.addSeparator()
        self.help_menu.addAction(self.act_about)
        self.btn_help.setMenu(self.help_menu)

        self.btn_theme = QToolButton()
        self.btn_theme.setObjectName("HelpBtn")
        self.btn_theme.setCursor(Qt.PointingHandCursor)
        self.btn_theme.setPopupMode(QToolButton.InstantPopup)
        self.theme_menu = QMenu(self.btn_theme)
        self.sec_mode = self._menu_header(self.theme_menu)
        self._mode_group = QActionGroup(self)
        self.act_modes = {}
        for m in th.MODES:
            a = QAction(self.theme_menu, checkable=True)
            a.triggered.connect(lambda _=False, m=m: self._set_theme(mode=m))
            self._mode_group.addAction(a)
            self.theme_menu.addAction(a)
            self.act_modes[m] = a
        self.theme_menu.addSeparator()
        self.sec_accent = self._menu_header(self.theme_menu)
        self._accent_group = QActionGroup(self)
        self.act_accents = {}
        for k, (_, _, txt, _) in th.ACCENTS.items():
            a = QAction(_swatch("#8b949e" if k == "neutral" else txt), "", self.theme_menu, checkable=True)
            a.triggered.connect(lambda _=False, k=k: self._set_theme(accent=k))
            self._accent_group.addAction(a)
            self.theme_menu.addAction(a)
            self.act_accents[k] = a
        self.btn_theme.setMenu(self.theme_menu)
        lay.addWidget(self.btn_theme)
        lay.addSpacing(4)
        lay.addWidget(self.btn_help)
        lay.addSpacing(6)
        lay.addWidget(self.lbl_lang)
        lay.addWidget(self.lang_cb)
        lay.addSpacing(10)
        lay.addWidget(ver)
        return hdr

    def _build_sidebar(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setObjectName("Sidebar")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFixedWidth(332)
        inner = QWidget()
        inner.setObjectName("SidebarInner")
        col = QVBoxLayout(inner)
        col.setContentsMargins(0, 0, 8, 0)
        col.setSpacing(12)

        # --- Bağlantı ---
        c = self.card_conn = Card()
        self.lbl_port = c.label()
        self.port_cb = QComboBox()
        self.port_cb.setMinimumContentsLength(10)
        self.port_cb.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.btn_refresh = QPushButton("⟳")
        self.btn_refresh.setObjectName("Icon")
        self.btn_refresh.clicked.connect(lambda: self._refresh_ports(manual=True))
        c.add(hrow(self.port_cb, self.btn_refresh))
        self.lbl_baud = c.label()
        self.baud_cb = QComboBox()
        self.baud_cb.setEditable(True)
        self.baud_cb.addItems(BAUDS)
        self.baud_cb.lineEdit().setValidator(QIntValidator(300, 20_000_000, self))
        c.add(self.baud_cb)
        self.lbl_mode = c.label()
        self.seg_mode = Segmented(["live", "dma"])
        self.seg_mode.changed.connect(self._on_mode_changed)
        c.add(self.seg_mode)
        self.lbl_mode_hint = hint_label()
        c.add(self.lbl_mode_hint)
        self.lbl_dfmt = c.label()
        self.seg_dfmt = Segmented(["text", "binary"])
        self.seg_dfmt.changed.connect(self._on_dfmt_changed)
        c.add(self.seg_dfmt)
        self.lbl_dfmt_hint = hint_label()
        c.add(self.lbl_dfmt_hint)
        self.chk_reconnect = QCheckBox()
        c.add(self.chk_reconnect)
        c.spacing(6)
        self.btn_connect = QPushButton()
        self.btn_connect.setObjectName("Primary")
        self.btn_connect.setCursor(Qt.PointingHandCursor)
        self.btn_connect.clicked.connect(self._toggle_connect)
        c.add_persistent(self.btn_connect)          # kart katlıyken de bağlan/kes düğmesi görünür
        col.addWidget(c)

        # --- Veri ayarları ---
        c = self.card_data = Card()
        self.lbl_fs = c.label()
        self.fs_spin = QDoubleSpinBox()
        self.fs_spin.setRange(0, 10_000_000)
        self.fs_spin.setDecimals(2)
        self.fs_spin.setSuffix(" Hz")
        self.fs_spin.setButtonSymbols(QDoubleSpinBox.NoButtons)
        c.add(self.fs_spin)
        self.lbl_seq = c.label()
        self.seq_cb = QComboBox()
        for k in ("auto", "on", "off"):
            self.seq_cb.addItem(k, k)
        c.add(self.seq_cb)
        self.lbl_idle = c.label()
        self.idle_spin = QDoubleSpinBox()
        self.idle_spin.setRange(0.2, 60)
        self.idle_spin.setDecimals(1)
        self.idle_spin.setSingleStep(0.5)
        self.idle_spin.setSuffix(" s")
        self.idle_spin.setButtonSymbols(QDoubleSpinBox.NoButtons)
        c.add(self.idle_spin)
        c.spacing(4)
        self.btn_units = QPushButton()
        self.btn_units.clicked.connect(self._edit_units)
        c.add(self.btn_units)
        self.btn_alarms = QPushButton()
        self.btn_alarms.clicked.connect(self._edit_alarms)
        c.add(self.btn_alarms)
        col.addWidget(c)

        # --- Kayıt ---
        c = self.card_save = Card()
        self.lbl_fmt = c.label()
        self.seg_fmt = Segmented(["xlsx", "csv", "txt"])
        self.seg_fmt.set_texts({"xlsx": "Excel", "csv": "CSV", "txt": "TXT"})
        c.add(self.seg_fmt)
        self.lbl_folder = c.label()
        self.folder_edit = QLineEdit()
        self.btn_browse = QPushButton()
        self.btn_browse.clicked.connect(self._browse_folder)
        c.add(hrow(self.folder_edit, self.btn_browse))
        self.lbl_prefix = c.label()
        self.prefix_edit = QLineEdit()
        c.add(self.prefix_edit)
        c.spacing(4)
        self.chk_auto = QCheckBox()
        self.chk_raw = QCheckBox()
        c.add(self.chk_auto)
        self.lbl_every = c.label()
        self.every_cb = QComboBox()
        for m in AUTOSAVE_MIN:
            self.every_cb.addItem(str(m), m)
        c.add(self.every_cb)
        self.chk_auto.toggled.connect(lambda on: (self.every_cb.setEnabled(on), self.lbl_every.setEnabled(on)))
        c.spacing(4)
        self.chk_trig = QCheckBox()
        c.add(self.chk_trig)
        self.trig_pre = QSpinBox()
        self.trig_pre.setRange(0, analysis.TRIG_PRE_MAX)
        self.trig_dur = QSpinBox()
        self.trig_dur.setRange(analysis.TRIG_DUR_MIN, analysis.TRIG_DUR_MAX)
        for sb in (self.trig_pre, self.trig_dur):
            sb.setSuffix(" s")
            sb.setButtonSymbols(QSpinBox.NoButtons)
        self.lbl_trig_pre, self.lbl_trig_dur = QLabel(), QLabel()
        for lb in (self.lbl_trig_pre, self.lbl_trig_dur):
            lb.setObjectName("FieldLabel")
        trow = QHBoxLayout()
        trow.setSpacing(6)
        trow.addWidget(self.lbl_trig_pre)
        trow.addWidget(self.trig_pre, 1)
        trow.addSpacing(4)
        trow.addWidget(self.lbl_trig_dur)
        trow.addWidget(self.trig_dur, 1)
        self.trig_row = QWidget()
        self.trig_row.setLayout(trow)
        trow.setContentsMargins(0, 0, 0, 0)
        c.add(self.trig_row)
        self.lbl_trig_hint = hint_label()
        c.add(self.lbl_trig_hint)
        self.chk_trig.toggled.connect(lambda *_: self._sync_trig_widgets())
        c.add(self.chk_raw)
        c.spacing(4)
        self.btn_save_quick = QPushButton()
        self.btn_save_quick.setObjectName("Accent")
        self.btn_save_quick.clicked.connect(self._save_now)
        self.btn_save_as = QPushButton()
        self.btn_save_as.clicked.connect(self._save_as)
        self.btn_open_folder = QPushButton()
        self.btn_open_folder.clicked.connect(self._open_folder)
        c.add(self.btn_open_folder)
        self.btn_open_saved = QPushButton()
        self.btn_open_saved.clicked.connect(self._open_saved_file)
        c.add(self.btn_open_saved)
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(self.btn_save_quick, 1)
        row.addWidget(self.btn_save_as, 1)
        c.add_persistent(row)                       # katlıyken de "Şimdi kaydet" görünür
        col.addWidget(c)
        col.addStretch(1)
        for card in (self.card_conn, self.card_data, self.card_save):
            card.toggled.connect(self._save_collapsed)

        scroll.setWidget(inner)
        return scroll

    def _build_main(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)

        tiles = QHBoxLayout()
        tiles.setSpacing(10)
        self.tile_rows, self.tile_rate, self.tile_missing, self.tile_dur, self.tile_status = (Tile() for _ in range(5))
        for tl in (self.tile_rows, self.tile_rate, self.tile_missing, self.tile_dur, self.tile_status):
            tiles.addWidget(tl, 1)
        v.addLayout(tiles)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(True)
        self.progress.setFixedHeight(14)
        self.progress.hide()
        v.addWidget(self.progress)

        # --- Sonuç bandı ---
        self.banner = QFrame()
        self.banner.setObjectName("Banner")
        bl = QHBoxLayout(self.banner)
        bl.setContentsMargins(14, 8, 8, 8)
        self.banner_lbl = QLabel()
        self.banner_lbl.setTextFormat(Qt.PlainText)
        self.banner_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.banner_open = QPushButton()
        self.banner_open.setObjectName("Link")
        self.banner_open.clicked.connect(lambda: self._open_path(self.last_saved))
        self.banner_folder = QPushButton()
        self.banner_folder.setObjectName("Link")
        self.banner_folder.clicked.connect(self._open_folder)
        self.banner_close = QPushButton("✕")
        self.banner_close.setObjectName("Link")
        self.banner_close.clicked.connect(self.banner.hide)
        bl.addWidget(self.banner_lbl, 1)
        bl.addWidget(self.banner_open)
        bl.addWidget(self.banner_folder)
        bl.addWidget(self.banner_close)
        self.banner.hide()
        v.addWidget(self.banner)

        split = self.splitter = QSplitter(Qt.Vertical)
        split.setChildrenCollapsible(False)
        split.addWidget(self._build_plot_panel())
        split.addWidget(self._build_terminal_panel())
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([480, 260])
        v.addWidget(split, 1)
        return w

    def _build_plot_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("Panel")
        v = QVBoxLayout(panel)
        v.setContentsMargins(12, 10, 12, 12)
        v.setSpacing(4)
        h = self.plot_hdr = PanelHeader()
        self.btn_plot_toggle = QToolButton()
        self.btn_plot_toggle.setObjectName("Chevron")
        self.btn_plot_toggle.setText("▾")
        self.btn_plot_toggle.setCursor(Qt.PointingHandCursor)
        self.btn_plot_toggle.clicked.connect(lambda: self._set_plot_collapsed(not self._plot_collapsed))
        h.lay.insertWidget(0, self.btn_plot_toggle)
        h.title.setCursor(Qt.PointingHandCursor)
        h.title.installEventFilter(self)
        self.lbl_x = h.add(QLabel())
        self.lbl_x.setObjectName("FieldLabel")
        self.x_cb = h.add(QComboBox())
        for k in ("auto", "sample", "time", "host"):
            self.x_cb.addItem(k, k)
        self.x_cb.currentIndexChanged.connect(self._invalidate_plot)
        self.lbl_win = h.add(QLabel())
        self.lbl_win.setObjectName("FieldLabel")
        self.win_cb = h.add(QComboBox())
        for s in WINDOWS:
            self.win_cb.addItem(str(s), s)
        self.win_cb.currentIndexChanged.connect(self._invalidate_plot)
        self.btn_fit = h.add(QPushButton())
        self.btn_fit.clicked.connect(self._fit_plot)
        self.btn_png = h.add(QPushButton())
        self.btn_png.clicked.connect(self._save_png)
        v.addWidget(h)

        pg.setConfigOptions(antialias=True, background=th.BG_PLOT, foreground=th.MUTED)
        self.plot = pg.PlotWidget()
        pi = self.plot.getPlotItem()
        pi.showGrid(x=True, y=True, alpha=0.12)
        pi.setClipToView(True)
        pi.setDownsampling(auto=True, mode="peak")
        self.legend = pi.addLegend(offset=(12, 8))
        self.legend.setVisible(False)

        # Analiz satırı: imleç, FFT, not
        self.plot_tools = QWidget()
        tools = QHBoxLayout(self.plot_tools)
        tools.setContentsMargins(0, 0, 0, 0)
        tools.setSpacing(6)
        self.btn_cursor = QPushButton()
        self.btn_cursor.setObjectName("Tool")
        self.btn_cursor.setCheckable(True)
        self.btn_cursor.toggled.connect(self._toggle_cursors)
        self.btn_fft = QPushButton()
        self.btn_fft.setObjectName("Tool")
        self.btn_fft.setCheckable(True)
        self.btn_fft.toggled.connect(self._toggle_fft)
        self.btn_note = QPushButton()
        self.btn_note.setObjectName("Tool")
        self.btn_note.clicked.connect(self._add_note)
        self.lbl_readout = QLabel()
        self.lbl_readout.setObjectName("Readout")
        self.lbl_readout.setTextFormat(Qt.PlainText)
        self.lbl_readout.setMinimumWidth(10)
        tools.addWidget(self.btn_cursor)
        tools.addWidget(self.btn_fft)
        tools.addWidget(self.btn_note)
        tools.addSpacing(8)
        tools.addWidget(self.lbl_readout, 1)
        v.addWidget(self.plot_tools)

        self.fft_plot = pg.PlotWidget()
        fpi = self.fft_plot.getPlotItem()
        fpi.showGrid(x=True, y=True, alpha=0.12)
        self.fft_plot.hide()
        self.plot_split = QSplitter(Qt.Vertical)
        self.plot_split.setChildrenCollapsible(False)
        self.plot_split.addWidget(self.plot)
        self.plot_split.addWidget(self.fft_plot)
        self.plot_split.setSizes([300, 200])
        self._style_plot()
        v.addWidget(self.plot_split, 1)
        self.plot_panel = panel

        for cb in (pi.ctrl.logXCheck, pi.ctrl.logYCheck):     # log eksen açılıp kapanınca sınırlar yeniden
            cb.toggled.connect(lambda *_: (self._invalidate_plot(), self._update_plot()))
        for cb in (fpi.ctrl.logXCheck, fpi.ctrl.logYCheck):
            cb.toggled.connect(lambda *_: self._apply_fft_limits())
        self._analysis_timer = QTimer(self, singleShot=True, interval=150, timeout=self._update_analysis)
        self.plot.getPlotItem().getViewBox().sigXRangeChanged.connect(lambda *_: self._analysis_timer.start())
        return panel

    def _build_terminal_panel(self) -> QWidget:
        panel = self.bottom_panel = QFrame()
        panel.setObjectName("Panel")
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(8, 8, 8, 8)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        outer.addWidget(self.tabs)

        corner = QWidget()
        ch = QHBoxLayout(corner)
        ch.setContentsMargins(0, 0, 4, 4)
        ch.setSpacing(8)
        self.chk_hex = QCheckBox()
        self.chk_hex.toggled.connect(self._on_hex_toggled)
        self.chk_pause = QCheckBox()
        self.btn_clear = QPushButton()
        self.btn_clear.clicked.connect(lambda: self.term.clear())
        for w_ in (self.chk_hex, self.chk_pause, self.btn_clear):
            ch.addWidget(w_)
        self.tabs.setCornerWidget(corner, Qt.TopRightCorner)
        self.tabs.currentChanged.connect(self._on_tab_changed)

        term_page = QWidget()
        v = QVBoxLayout(term_page)
        v.setContentsMargins(4, 8, 4, 4)
        v.setSpacing(6)

        self.term = QPlainTextEdit()
        self.term.setReadOnly(True)
        self.term.setMaximumBlockCount(3000)
        self.term.setLineWrapMode(QPlainTextEdit.NoWrap)
        mono = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        for fam in ("Cascadia Mono", "Consolas", "JetBrains Mono", "SF Mono", "Menlo", "DejaVu Sans Mono"):
            if fam in QFontDatabase.families():
                mono = QFont(fam)
                break
        mono.setPointSizeF(9.5)
        self.term.setFont(mono)
        v.addWidget(self.term, 1)

        self.send_edit = QLineEdit()
        self.send_edit.returnPressed.connect(self._send)
        self.send_edit.installEventFilter(self)       # ↑ / ↓ komut geçmişi
        self.chk_hex_send = QCheckBox()
        self.eol_cb = QComboBox()
        self.eol_cb.addItem("\\r\\n", "\r\n")
        self.eol_cb.addItem("\\n", "\n")
        self.eol_cb.addItem("-", "")
        self.btn_send = QPushButton()
        self.btn_send.setObjectName("Accent")
        self.btn_send.clicked.connect(self._send)
        self.qc_row = QHBoxLayout()
        self.qc_row.setSpacing(6)
        v.addLayout(self.qc_row)
        v.addLayout(hrow(self.send_edit, self.chk_hex_send, self.eol_cb, self.btn_send))
        self.tabs.addTab(term_page, "")

        stats_page = QWidget()
        sv_ = QVBoxLayout(stats_page)
        sv_.setContentsMargins(4, 8, 4, 4)
        self.lbl_stats_scope = QLabel()
        self.lbl_stats_scope.setObjectName("Hint")
        self.stats_table = QTableWidget(0, 8)
        self.stats_table.verticalHeader().setVisible(False)
        self.stats_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.stats_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        sv_.addWidget(self.lbl_stats_scope)
        sv_.addWidget(self.stats_table, 1)
        self.tabs.addTab(stats_page, "")

        notes_page = QWidget()
        nv = QVBoxLayout(notes_page)
        nv.setContentsMargins(4, 8, 4, 4)
        self.notes_list = QListWidget()
        self.notes_list.setObjectName("Notes")
        self.lbl_notes_empty = QLabel()
        self.lbl_notes_empty.setObjectName("Hint")
        self.lbl_notes_empty.setWordWrap(True)
        nv.addWidget(self.lbl_notes_empty)
        nv.addWidget(self.notes_list, 1)
        self.tabs.addTab(notes_page, "")
        self._rebuild_quick()
        return panel

    def _build_statusbar(self) -> None:
        sb = self.statusBar()
        sb.setSizeGripEnabled(False)
        self.status_dot = QLabel("●")
        self.status_lbl = QLabel()
        sb.addWidget(self.status_dot)
        sb.addWidget(self.status_lbl, 1)
        self.saved_btn = QPushButton()
        self.saved_btn.setObjectName("Link")
        self.saved_btn.clicked.connect(lambda: self._open_path(self.last_saved))
        self.saved_btn.hide()
        sb.addPermanentWidget(self.saved_btn)
        self._set_status_dot(False)

    # ================================================================ METİNLER
    def retranslate(self) -> None:
        t = self.t
        self.lbl_title_a.setText(t("title_a"))
        self.lbl_title.setText(t("title_b"))
        self.setWindowTitle(t("app_name"))
        self.lbl_lang.setText(t("language"))
        self.btn_help.setToolTip(t("help_btn") + "  (F1)")
        self.act_guide.setText(t("menu_guide"))
        self.act_updates.setText(t("menu_updates"))
        self.act_report.setText(t("menu_report"))
        self.act_about.setText(t("menu_about"))
        self.act_reset.setText(t("menu_reset"))
        self.btn_theme.setToolTip(t("theme_btn"))
        self.sec_mode.setText(t("theme_mode"))
        self.sec_accent.setText(t("theme_color"))
        for m, a in self.act_modes.items():
            a.setText(t("tm_" + m))
        for k, a in self.act_accents.items():
            a.setText(t("ac_" + k))
        self._sync_theme_menu()
        if getattr(self, "_help_dlg", None) is not None:
            self._help_dlg.set_lang(self.lang)
        self.card_conn.title.setText(t("card_conn"))
        self.lbl_port.setText(t("port"))
        self.btn_refresh.setToolTip(t("refresh"))
        self.lbl_baud.setText(t("baud"))
        self.lbl_mode.setText(t("mode"))
        self.seg_mode.set_texts({"live": t("mode_live"), "dma": t("mode_dma")})
        self.lbl_mode_hint.setText(t("mode_" + self.seg_mode.value() + "_hint"))
        self.lbl_dfmt.setText(t("dfmt"))
        self.seg_dfmt.set_texts({"text": t("dfmt_text"), "binary": t("dfmt_binary")})
        self.lbl_dfmt_hint.setText(t("dfmt_" + self.seg_dfmt.value() + "_hint"))
        self.chk_reconnect.setText(t("autoreconnect"))
        self.chk_reconnect.setToolTip(t("autoreconnect_tip"))
        self.btn_connect.setToolTip("F5")
        self._style_connect_button()

        self.card_data.title.setText(t("card_data"))
        self.lbl_fs.setText(t("fs"))
        self.fs_spin.setSpecialValueText(t("fs_unknown"))
        self.fs_spin.setToolTip(t("fs_tip"))
        self.lbl_fs.setToolTip(t("fs_tip"))
        self.lbl_seq.setText(t("seq"))
        for i, k in enumerate(("seq_auto", "seq_on", "seq_off")):
            self.seq_cb.setItemText(i, t(k))
        self.seq_cb.setToolTip(t("seq_tip"))
        self.lbl_idle.setText(t("idle"))
        self.idle_spin.setToolTip(t("idle_tip"))

        self.card_save.title.setText(t("card_save"))
        self.lbl_fmt.setText(t("format"))
        self.seg_fmt.setToolTip(t("format_tip"))
        self.lbl_folder.setText(t("folder"))
        self.btn_browse.setText(t("browse"))
        self.lbl_prefix.setText(t("prefix"))
        self.chk_auto.setText(t("autosave"))
        self.lbl_every.setText(t("as_every"))
        self.lbl_every.setToolTip(t("as_tip"))
        self.every_cb.setToolTip(t("as_tip"))
        unit = "dk" if self.lang == "tr" else "min"
        for i, m in enumerate(AUTOSAVE_MIN):
            self.every_cb.setItemText(i, t("as_off") if m == 0 else f"{m} {unit}")
        self.btn_save_quick.setText(t("save_quick"))
        self.btn_save_quick.setToolTip(t("save_quick_tip") + "  (Ctrl+S)")
        self.chk_trig.setText(t("trig_chk"))
        self.chk_trig.setToolTip(t("trig_tip"))
        self.lbl_trig_pre.setText(t("trig_pre"))
        self.lbl_trig_dur.setText(t("trig_dur"))
        self.lbl_trig_hint.setText(t("trig_hint"))
        self._sync_trig_widgets()
        self.chk_raw.setText(t("rawbackup"))
        self.chk_raw.setToolTip(t("rawbackup_tip"))
        self.btn_save_as.setText(t("save_now"))
        self.btn_save_as.setToolTip("Ctrl+Shift+S")
        self.btn_open_folder.setText(t("open_folder"))
        self.btn_open_saved.setText(t("open_saved"))
        self.btn_open_saved.setToolTip("Ctrl+O")

        self.tile_rows.label.setText(t("st_rows").upper())
        self.tile_rate.label.setText(t("st_rate").upper())
        self.tile_missing.label.setText(t("st_missing").upper())
        self.tile_dur.label.setText(t("st_duration").upper())
        self.tile_status.label.setText(t("st_status").upper())

        self.plot_hdr.title.setText(t("plot_title"))
        self.btn_plot_toggle.setToolTip(t("plot_show" if self._plot_collapsed else "plot_hide"))
        self.lbl_x.setText(t("x_axis"))
        for i, k in enumerate(("x_auto", "x_sample", "x_time", "x_host")):
            self.x_cb.setItemText(i, t(k))
        self.lbl_win.setText(t("window"))
        for i, s in enumerate(WINDOWS):
            self.win_cb.setItemText(i, t("win_all") if s == 0 else (f"{s} s" if s < 60 else f"{s // 60} dk" if self.lang == "tr" else f"{s // 60} min"))
        self.btn_fit.setText(t("fit"))
        self.btn_png.setText(t("save_png"))
        self.plot.getPlotItem().setLabel("left", t("value"))

        self.tabs.setTabText(0, t("tab_terminal"))
        self.tabs.setTabText(1, t("tab_stats"))
        self.tabs.setTabText(2, t("tab_notes"))
        self.stats_table.setHorizontalHeaderLabels([t(k) for k in (
            "st_ch", "st_last", "st_min", "st_max", "st_mean", "st_rms", "st_std", "st_peak")])
        self.lbl_notes_empty.setText(t("notes_empty"))
        self.btn_cursor.setText(t("cursor_btn"))
        self.btn_cursor.setToolTip(t("cursor_tip"))
        self.btn_fft.setText(t("fft_btn"))
        self.btn_fft.setToolTip(t("fft_tip"))
        self.btn_note.setText(t("note_btn"))
        self.btn_note.setToolTip(t("note_tip") + "  (Ctrl+M)")
        self.btn_units.setText(t("units_btn"))
        self.btn_units.setToolTip(t("units_help"))
        self._style_alarm_button()
        self.fft_plot.getPlotItem().setLabel("left", t("fft_y"))
        self._rebuild_quick()
        self._update_readout()
        self.chk_pause.setText(t("pause"))
        self.chk_hex.setText(t("hex_view"))
        self.chk_hex.setToolTip(t("hex_view_tip"))
        self.chk_hex_send.setText(t("hex_send"))
        self.chk_hex_send.setToolTip(t("hex_send_tip"))
        self.btn_clear.setText(t("clear"))
        self.btn_clear.setToolTip("Ctrl+L")
        self.send_edit.setPlaceholderText(t("send_placeholder"))
        self.send_edit.setToolTip(t("history_tip"))
        self.btn_send.setText(t("send"))
        self.eol_cb.setItemText(2, t("eol_none"))
        self.banner_open.setText(t("open_file"))
        self.banner_folder.setText(t("open_folder"))

        idx = self.port_cb.findData(DEMO_PORT)
        if idx >= 0:
            self.port_cb.setItemText(idx, t("demo_port"))
        self._plot_sig = None
        self._update_status_text()

    # ================================================================ TEMA
    @staticmethod
    def _menu_header(menu) -> QAction:
        """Menüde başlık satırı (tıklanamaz, kalın)."""
        a = QAction(menu)
        a.setEnabled(False)
        f = a.font()
        f.setBold(True)
        a.setFont(f)
        menu.addAction(a)
        return a

    def _style_plot(self) -> None:
        for pw in (self.plot, getattr(self, "fft_plot", None)):
            if pw is None:
                continue
            pw.setBackground(th.BG_PLOT)
            pi = pw.getPlotItem()
            for ax in ("left", "bottom"):
                a = pi.getAxis(ax)
                a.setPen(pg.mkPen(th.BORDER))
                a.setTextPen(pg.mkPen(th.MUTED))
        for k, ln in enumerate(getattr(self, "_cursor_lines", [])):
            color = th.ACCENT_TXT if k == 0 else "#e3b341"
            ln.setPen(pg.mkPen(color, width=1.5, style=Qt.DashLine))
            ln.label.setColor(color)
            ln.label.fill = pg.mkBrush(th.BG_PANEL)
        for i, c in enumerate(getattr(self, "_fft_curves", {}).values()):
            c.setPen(pg.mkPen(th.SERIES[i % len(th.SERIES)], width=1.4))
        for ln in getattr(self, "_note_items", {}).values():
            ln.label.fill = pg.mkBrush(th.BG_PANEL)
            ln.label.update()
        self.legend.setBrush(pg.mkBrush(*th.LEGEND_BG))
        self.legend.setPen(pg.mkPen(th.BORDER))
        self.legend.setLabelTextColor(th.TEXT)

    def _sync_theme_menu(self) -> None:
        if self._theme_mode in self.act_modes:
            self.act_modes[self._theme_mode].setChecked(True)
        if self._accent in self.act_accents:
            self.act_accents[self._accent].setChecked(True)
        self.btn_theme.setIcon(_swatch(th.ACCENT_TXT, 14))

    def _set_theme(self, mode: str | None = None, accent: str | None = None) -> None:
        if mode is not None:
            self._theme_mode = mode
        if accent is not None:
            self._accent = accent
        self._apply_theme()

    def _apply_theme(self, save: bool = True) -> None:
        th.apply(th.resolve_mode(self._theme_mode), self._accent)
        QApplication.instance().setStyleSheet(th.build_qss(ASSETS))
        for w in QApplication.topLevelWidgets():       # açık pencerelerin başlık çubukları da
            if w.isVisible():
                style_title_bar(w)
        self._style_plot()
        for i, c in enumerate(self._curves.values()):   # yakınlaştırma korunur, sadece renkler değişir
            c.setPen(pg.mkPen(th.SERIES[i % len(th.SERIES)], width=1.6))
        self._plot_sig = None
        self._set_status_dot(self.engine is not None and not self.engine.reconnecting)
        self._sync_theme_menu()
        if getattr(self, "_help_dlg", None) is not None:
            self._help_dlg.refresh_theme()
        self._update_stats(force=True)
        if save:
            self.settings.setValue("theme_mode", self._theme_mode)
            self.settings.setValue("accent", self._accent)

    def _set_lang(self, lang: str) -> None:
        if lang == self.lang:
            return
        self.lang = lang
        self.t = T(lang)
        self.settings.setValue("lang", lang)
        self.retranslate()
        self._update_stats(force=True)

    # ================================================================ AYARLAR
    def _default_folder(self) -> str:
        docs = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation) or str(Path.home())
        return os.path.join(docs, "Oskilo")

    def _sv(self, key: str, default, kind=str):
        """Ayarı güvenle okur: bozuk/elle değiştirilmiş değer uygulamayı çökertmez."""
        try:
            v = self.settings.value(key, default)
            if v is None or v == "":
                return default
            if kind is bool:
                return str(v).lower() == "true"
            return kind(v)
        except (TypeError, ValueError):
            return default

    def _load_settings(self) -> None:
        sv = self._sv
        self.baud_cb.setCurrentText(str(sv("baud", 115200, int)))
        self.seg_mode.set_value(sv("mode", "dma") if sv("mode", "dma") in ("live", "dma") else "dma")
        self.fs_spin.setValue(sv("fs", 0.0, float))
        self.seq_cb.setCurrentIndex(max(0, self.seq_cb.findData(sv("seq", "auto"))))
        self.idle_spin.setValue(sv("idle", 1.0, float))
        self.seg_fmt.set_value(sv("fmt", "xlsx") if sv("fmt", "xlsx") in FORMATS else "xlsx")
        self.folder_edit.setText(sv("folder", "") or self._default_folder())
        self.prefix_edit.setText(sv("prefix", "") or self.t("default_prefix"))
        self.chk_auto.setChecked(sv("autosave", True, bool))
        i = self.every_cb.findData(sv("autosave_every", 5, int))
        self.every_cb.setCurrentIndex(i if i >= 0 else AUTOSAVE_MIN.index(5))
        self.every_cb.setEnabled(self.chk_auto.isChecked())
        self.lbl_every.setEnabled(self.chk_auto.isChecked())
        self.chk_raw.setChecked(sv("raw", True, bool))
        self.chk_trig.setChecked(sv("trig_on", False, bool))
        self.trig_pre.setValue(max(0, min(analysis.TRIG_PRE_MAX, sv("trig_pre", 5, int))))
        self.trig_dur.setValue(max(analysis.TRIG_DUR_MIN, min(analysis.TRIG_DUR_MAX, sv("trig_dur", 60, int))))
        self._sync_trig_widgets()
        self.chk_reconnect.setChecked(sv("reconnect", True, bool))
        self.seg_dfmt.set_value("binary" if sv("data_format", "text") == "binary" else "text")
        self._on_dfmt_changed(self.seg_dfmt.value())
        self.chk_hex.setChecked(sv("hex", False, bool))
        self.btn_fft.setChecked(sv("fft_on", False, bool))
        self.tabs.setCurrentIndex(max(0, min(2, sv("bottom_tab", 0, int))))
        self.x_cb.setCurrentIndex(max(0, self.x_cb.findData(sv("xaxis", "auto"))))
        i = self.win_cb.findData(sv("window", 30, int))
        self.win_cb.setCurrentIndex(i if i >= 0 else 1)
        self.eol_cb.setCurrentIndex(max(0, self.eol_cb.findData(sv("eol", "\r\n"))))
        try:
            geo = self.settings.value("geometry")
            if geo is not None:
                self.restoreGeometry(geo)
            sp = self.settings.value("splitter")
            if sp is not None:
                self.splitter.restoreState(sp)
            ps = self.settings.value("plot_split")
            if ps is not None:
                self.plot_split.restoreState(ps)
        except (TypeError, ValueError):
            pass
        try:
            col = json.loads(sv("collapsed", "{}"))
            col = col if isinstance(col, dict) else {}
        except (TypeError, ValueError):
            col = {}
        for key, card in self._cards().items():
            card.set_collapsed(bool(col.get(key, False)))
        self._set_plot_collapsed(bool(col.get("plot", False)), save=False)

    def _reset_settings(self) -> None:
        t = self.t
        if self.engine is not None:
            self._msg("info", t("menu_reset"), t("reset_busy"))
            return
        if self._ask(t("menu_reset"), t("reset_ask"), t("menu_reset")) is not True:
            return
        system.reset_settings(self.settings)
        self.settings.setValue("help_seen", "true")
        self.settings.setValue("unblock_asked", "true")
        self._history, self._hist_pos = [], 0
        self._alarms, self._units = [], {}
        self._alarm_armed, self._alarm_fired_t, self._alarm_pending = {}, {}, {}
        self._alarm_sig = None
        word = "GONDER" if self.lang == "tr" else "SEND"
        self._qcmds = [{"label": word, "cmd": word, "hex": False, "eol": "\r\n", "repeat": 0.0}]
        self._rebuild_quick()
        self._style_alarm_button()
        self._curve_owner = None
        self._theme_mode, self._accent = "system", th.DEFAULT_ACCENT
        self._apply_theme(save=False)
        self._load_settings()
        self.resize(1360, 860)
        self._set_plot_collapsed(False, save=False)
        self.splitter.setSizes([480, 260])
        self._set_lang("tr" if self.lang_cb.currentIndex() == 0 else "en")
        self._log(t("reset_done"), "ok")

    def _save_settings(self) -> None:
        s = self.settings
        s.setValue("port", self.port_cb.currentData() or "")
        s.setValue("baud", self.baud_cb.currentText())
        s.setValue("mode", self.seg_mode.value())
        s.setValue("fs", self.fs_spin.value())
        s.setValue("seq", self.seq_cb.currentData())
        s.setValue("idle", self.idle_spin.value())
        s.setValue("fmt", self.seg_fmt.value())
        s.setValue("folder", self.folder_edit.text())
        s.setValue("prefix", self.prefix_edit.text())
        s.setValue("autosave", "true" if self.chk_auto.isChecked() else "false")
        s.setValue("autosave_every", self.every_cb.currentData())
        s.setValue("raw", "true" if self.chk_raw.isChecked() else "false")
        s.setValue("trig_on", "true" if self.chk_trig.isChecked() else "false")
        s.setValue("trig_pre", self.trig_pre.value())
        s.setValue("trig_dur", self.trig_dur.value())
        s.setValue("reconnect", "true" if self.chk_reconnect.isChecked() else "false")
        s.setValue("data_format", self._data_format)
        s.setValue("hex", "true" if self.chk_hex.isChecked() else "false")
        s.setValue("history", json.dumps(self._history[-HISTORY_MAX:], ensure_ascii=False))
        s.setValue("xaxis", self.x_cb.currentData())
        s.setValue("window", self.win_cb.currentData())
        s.setValue("eol", self.eol_cb.currentData())
        s.setValue("geometry", self.saveGeometry())
        if not self._plot_collapsed:           # gizliyken küçülmüş hâli değil, açık hâli hatırlansın
            s.setValue("splitter", self.splitter.saveState())
        s.setValue("collapsed", json.dumps(self._collapsed_state()))
        s.setValue("lang", self.lang)
        s.setValue("theme_mode", self._theme_mode)
        s.setValue("accent", self._accent)
        s.setValue("units", json.dumps(self._units, ensure_ascii=False))
        s.setValue("alarms", json.dumps(self._alarms, ensure_ascii=False))
        s.setValue("quick_cmds", json.dumps(self._qcmds, ensure_ascii=False))
        s.setValue("fft_on", "true" if self.btn_fft.isChecked() else "false")
        s.setValue("bottom_tab", self.tabs.currentIndex())
        s.setValue("plot_split", self.plot_split.saveState())

    # ================================================================ PORTLAR
    def _refresh_ports(self, initial=False, silent=False, manual=False) -> None:
        if self.engine is not None and not manual:
            return
        try:
            ports = list_ports()
        except Exception:
            ports = []
        sig = tuple((p, d) for p, d, _ in ports)
        if sig == self._port_sig and not initial:
            if manual:
                self._log(self.t("msg_ports", n=len(ports)), "info")
            return
        self._port_sig = sig
        cur = self.port_cb.currentData()
        if initial:
            cur = self._sv("port", "")
        self.port_cb.blockSignals(True)
        self.port_cb.clear()
        for dev, desc, is_stm in ports:
            label = dev if desc == dev or desc in ("n/a", "") else f"{dev} — {desc}"
            self.port_cb.addItem(("★ " if is_stm else "") + label, dev)
            self.port_cb.setItemData(self.port_cb.count() - 1, _tip(desc), Qt.ToolTipRole)
        self.port_cb.addItem(self.t("demo_port"), DEMO_PORT)
        i = self.port_cb.findData(cur) if cur else -1
        if i < 0:
            stm = next((k for k, (_, _, s) in enumerate(ports) if s), None)
            i = stm if stm is not None else 0
            if initial and not ports:
                i = self.port_cb.findData(DEMO_PORT)
        self.port_cb.setCurrentIndex(i)
        self.port_cb.blockSignals(False)
        if not silent or manual:
            self._log(self.t("msg_ports", n=len(ports)) if ports else self.t("no_port"),
                      "info" if ports else "warn")
        elif ports and len(ports) > 0 and not initial:
            self._log(self.t("msg_ports", n=len(ports)), "info")

    # ================================================================ BAĞLANTI
    def _on_mode_changed(self, mode: str) -> None:
        self.lbl_mode_hint.setText(self.t("mode_" + mode + "_hint"))
        self.idle_spin.setEnabled(mode == "dma" and self.engine is None)
        self.lbl_idle.setEnabled(mode == "dma")
        self.win_cb.setEnabled(mode == "live")
        self._plot_sig = None
        self._sync_trig_widgets()

    def _sync_trig_widgets(self) -> None:
        """Tetikli kayıt ayarları: sadece canlı modda ve bağlı değilken değiştirilebilir."""
        if not hasattr(self, "chk_trig"):
            return
        live = self.seg_mode.value() == "live"
        idle = self.engine is None
        self.chk_trig.setEnabled(live and idle)
        on = self.chk_trig.isChecked()
        for w in (self.trig_pre, self.trig_dur, self.lbl_trig_pre, self.lbl_trig_dur):
            w.setEnabled(live and idle and on)
        self.trig_row.setVisible(on)
        self.lbl_trig_hint.setVisible(on)
        self.chk_trig.setToolTip(self.t("trig_tip") if live else self.t("trig_live_only"))

    @property
    def _data_format(self) -> str:
        return self.seg_dfmt.value() if hasattr(self, "seg_dfmt") else "text"

    def _on_dfmt_changed(self, fmt: str) -> None:
        self.lbl_dfmt_hint.setText(self.t("dfmt_" + fmt + "_hint"))
        binary_on = fmt == "binary"
        self.seq_cb.setEnabled(not binary_on and self.engine is None)   # ikili çerçevede sıra no hep var
        self.lbl_seq.setEnabled(not binary_on)

    def _style_connect_button(self) -> None:
        on = self.engine is not None
        self.btn_connect.setText(("■  " + self.t("disconnect")) if on else ("▶  " + self.t("connect")))
        self.btn_connect.setObjectName("Danger" if on else "Primary")
        self.btn_connect.style().unpolish(self.btn_connect)
        self.btn_connect.style().polish(self.btn_connect)

    def _toggle_connect(self) -> None:
        if self.engine is None:
            self._connect()
        else:
            self._disconnect()

    def _connect(self) -> None:
        t = self.t
        port = self.port_cb.currentData()
        if not port:
            self._msg("warn", t("err_title"), t("no_port"))
            return
        if not self._confirm_discard():
            return
        try:
            baud = int(self.baud_cb.currentText() or 115200)
        except ValueError:
            baud = 115200
        mode = self.seg_mode.value()
        dfmt = self._data_format
        try:
            ser = open_port(port, baud, mode, binary=dfmt == "binary")
        except Exception as e:
            self._log(t("msg_open_err", err=e), "error")
            self._msg("error", t("err_title"), t("msg_open_err", err=e))
            return

        raw_path = None
        folder = self.folder_edit.text().strip() or self._default_folder()
        if self.chk_raw.isChecked():
            stamp = _dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            safe_port = "".join(ch for ch in os.path.basename(port) if ch.isalnum()) or "port"
            ext = ".bin" if dfmt == "binary" else ".txt"
            raw_path = os.path.join(folder, "ham", f"ham_{stamp}_{safe_port}{ext}")

        seq_mode = "on" if dfmt == "binary" else self.seq_cb.currentData()
        prefix = t("channel_prefix")
        fs = self.fs_spin.value()
        port_name = "DEMO" if port == DEMO_PORT else port

        trig = mode == "live" and self.chk_trig.isChecked()

        def factory():
            s = Session(mode=mode, seq_mode=seq_mode, channel_prefix=prefix,
                        port=port_name, baud=baud, sample_rate=fs)
            s.seq32 = dfmt == "binary"          # ikili çerçevede sıra no 32 bit
            s.ephemeral = trig                  # tetikli kayıt: sürekli akış sadece arabellek
            return s

        reopen = None
        if self.chk_reconnect.isChecked() and port != DEMO_PORT:
            reopen = lambda: open_port(port, baud, mode, binary=dfmt == "binary")  # noqa: E731

        self.engine = Engine(ser, mode=mode, session_factory=factory, raw_path=raw_path,
                             idle_timeout=self.idle_spin.value(), reopen=reopen, data_format=dfmt)
        self.engine.hex_on = self.chk_hex.isChecked()
        self.engine.start()
        self._conn_t0 = time.time()
        self._conn_info = (port_name, baud, mode)
        self._alarm_armed, self._alarm_fired_t, self._alarm_pending = {}, {}, {}
        self._trig, self._trig_tail, self._trig_count = None, None, 0
        self._conn_trig = trig
        self._trig_pre_s, self._trig_dur_s = self.trig_pre.value(), self.trig_dur.value()
        self._rate.clear()
        self.view = self.engine.current
        self.banner.hide()
        self._plot_sig = None
        self._set_controls_enabled(False)
        self._style_connect_button()
        self._set_status_dot(True)
        self._update_status_text()
        self._log(t("msg_connected", port=port_name, baud=baud, mode=t("mode_" + mode)), "ok")
        if dfmt == "binary":
            self._log(t("msg_binary_on"), "muted")
        if trig:
            n_rec = sum(1 for a in self._alarms if a["on"] and a.get("rec"))
            if n_rec:
                self._log(t("msg_trig_armed", n=n_rec, pre=self._trig_pre_s, dur=self._trig_dur_s), "info")
            else:
                self._log(t("msg_trig_noalarm"), "warn")
                self._show_banner(t("msg_trig_noalarm"), "bad", show_open=False)
        if raw_path:
            self._log(t("msg_raw", path=raw_path), "muted")
        self._check_disk(folder)
        self._last_autosave = self._last_disk = time.monotonic()
        if system.keep_awake(True):
            self._log(t("msg_keep_awake"), "muted")

    def _check_disk(self, folder: str, periodic: bool = False) -> None:
        p = os.path.abspath(folder)
        while p and not os.path.exists(p):
            parent = os.path.dirname(p)
            if parent == p:
                return
            p = parent
        try:
            free_mb = shutil.disk_usage(p).free // (1024 * 1024)
        except OSError:
            return
        low = free_mb < DISK_LOW_MB
        if low and not (periodic and self._disk_low):    # test sürerken aynı uyarıyı tekrar tekrar gösterme
            self._log(self.t("disk_low", mb=_fmt_int(free_mb)), "warn")
            self._show_banner(self.t("disk_low", mb=_fmt_int(free_mb)), "bad", show_open=False)
        self._disk_low = low

    def _disconnect(self, lost: str | None = None) -> None:
        eng = self.engine
        if eng is None:
            return
        self.engine = None
        system.keep_awake(False)
        self._stop_repeats()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            eng.stop()
        finally:
            QApplication.restoreOverrideCursor()
        self._drain_terminal(eng)
        self._drain_done(eng)
        if self._trig is not None:            # bağlantı kesilince süren tetikli kayıt elde olanla kaydedilir
            self._trig_finish(cut=True)
        self._conn_trig = False
        self._trig_tail = None
        self._set_controls_enabled(True)
        self._style_connect_button()
        self._set_status_dot(False)
        self.progress.hide()
        self._update_status_text()
        if lost:
            self._log(self.t("msg_lost", err=lost), "error")
        else:
            self._log(self.t("msg_disconnected"), "warn")
        self._plot_sig = None
        self._update_stats(force=True)

    def _set_controls_enabled(self, on: bool) -> None:
        for w in (self.port_cb, self.btn_refresh, self.baud_cb, self.seg_mode, self.seg_dfmt, self.fs_spin,
                  self.seq_cb, self.btn_open_saved, self.chk_raw, self.chk_reconnect):
            w.setEnabled(on)
        if on and self._data_format == "binary":
            self.seq_cb.setEnabled(False)
        self.idle_spin.setEnabled(on and self.seg_mode.value() == "dma")
        self._sync_trig_widgets()
        self.send_edit.setEnabled(not on)
        self.btn_send.setEnabled(not on)
        self.eol_cb.setEnabled(not on)
        for b in getattr(self, "_qc_buttons", []):
            b.setEnabled(not on)
        if on:
            self._stop_repeats()

    # ================================================================ DÖNGÜ
    def _tick(self) -> None:
        self._drain_exports()
        try:
            while True:
                fn, args = self._bg_q.get_nowait()
                fn(*args)
        except queue.Empty:
            pass
        eng = self.engine
        if eng is not None:
            try:
                while True:
                    kind, msg = eng.events_q.get_nowait()
                    if kind == "disconnected":
                        self._disconnect(lost=msg or "?")
                        return
                    if kind == "stopped":          # işlemci beklenmedik şekilde durdu
                        self._disconnect(lost=msg or "processor")
                        return
                    if kind == "lost":
                        self._log(self.t("msg_lost_retry_trig" if self._conn_trig else "msg_lost_retry", err=msg or "?"), "warn")
                        self._set_status_dot(False)
                    elif kind == "reconnected":
                        self._log(self.t("msg_reconnected"), "ok")
                        self._set_status_dot(True)
                    elif kind == "skipped":
                        self._log(self.t("msg_bin_skipped", n=msg), "warn")
                    elif kind == "raw_part":
                        self._log(self.t("msg_raw_part", path=msg), "muted")
                    elif kind == "error" and msg.startswith("raw:"):
                        self._log(self.t("msg_raw_err", err=msg[4:]), "error")
                    elif kind == "error":
                        self._log(msg, "error")
            except queue.Empty:
                pass
            self._drain_done(eng)
            if not self.chk_pause.isChecked():
                self._drain_terminal(eng)
            if not self.chk_hex.isChecked() and eng.hex:
                eng.hex.clear()
            cur = eng.current
            if cur is not self.view and (cur.n > 0 or cur.saw_start):
                self.view = cur
                self._plot_sig = None
                if self.banner.isVisible() and self.banner_session is not cur:
                    self.banner.hide()
            now = time.time()
            self._rate.append((now, eng.total_lines, eng.total_bytes))
            while self._rate and now - self._rate[0][0] > 1.0:
                self._rate.popleft()
        self._update_stats()

    def _on_hex_toggled(self, on: bool) -> None:
        if self.engine is not None:
            self.engine.hex_on = on
            self.engine.hex.clear()

    def _drain_terminal(self, eng: Engine) -> None:
        if self.chk_hex.isChecked():
            eng.term.clear()
            eng.term_dropped = 0
            chunks = []
            try:
                while True:
                    chunks.append(eng.hex.popleft())
            except IndexError:
                pass
            if not chunks:
                return
            data = b"".join(chunks)
            if len(data) > HEX_MAX_PER_TICK:
                self._log(self.t("term_dropped", n=_fmt_int(len(data) - HEX_MAX_PER_TICK)) + " (byte)",
                          "muted", stamp=False)
                data = data[-HEX_MAX_PER_TICK:]
            sb = self.term.verticalScrollBar()
            at_bottom = sb.value() >= sb.maximum() - 4
            self.term.appendPlainText("\n".join(_hex_lines(data)))
            if at_bottom:
                sb.setValue(sb.maximum())
            return
        dq = eng.term
        if not dq:
            return
        lines = []
        try:
            while True:
                lines.append(dq.popleft())
        except IndexError:
            pass
        dropped = eng.term_dropped
        eng.term_dropped = 0
        if len(lines) > TERM_MAX_PER_TICK:
            dropped += len(lines) - TERM_MAX_PER_TICK
            lines = lines[-TERM_MAX_PER_TICK:]
        sb = self.term.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 4
        if dropped:
            self._log(self.t("term_dropped", n=_fmt_int(dropped)), "muted", stamp=False)
        self.term.appendPlainText("\n".join(lines))
        if at_bottom:
            sb.setValue(sb.maximum())

    def _drain_done(self, eng: Engine) -> None:
        try:
            while True:
                s = eng.done_q.get_nowait()
                self._alarm_scan_session(s, live=False)    # son satırlarına kadar (hızlı aktarımlar dahil)
                self._alarm_scanned.pop(s, None)
                self._trig_session_finished(s)
                if s.n > 0 or s.saw_start or s.bad_lines:
                    self._on_session_done(s)
        except queue.Empty:
            pass

    def _on_session_done(self, s: Session) -> None:
        t = self.t
        if s.ephemeral:
            # Tetikli kayıt modu: sürekli akış kaydedilmez; bölününce sessizce bırakılır (tetikler ayrı dosya)
            if s.finish_reason not in ("segment", "lost") and s.n:
                self.view = s
                self._plot_sig = None
                self._log(t("trig_buffer"), "muted")
            return
        self.banner_session = s
        if s.n == 0:
            txt = t("banner_done_empty")
            self._log(txt, "error")
            self._show_banner(txt, "bad", show_open=False)
            return
        self.view = s
        self._plot_sig = None
        if s.integrity_known and not s.integrity_ok:
            txt, state = t("banner_done_bad", n=_fmt_int(s.n), m=_fmt_int(s.missing), g=len(s.gaps)), "bad"
        elif s.integrity_known:
            txt, state = t("banner_done_ok", n=_fmt_int(s.n)), "ok"
        else:
            txt, state = t("banner_done_unknown", n=_fmt_int(s.n)), "info"
        if s.bad_lines:
            txt += "  ·  " + t("banner_bad_lines", b=_fmt_int(s.bad_lines))
            if state == "ok":
                state = "bad"
        if s.dropped_channels:
            txt += "  ·  " + t("banner_dropped_ch")
        if s.crc_errors:
            txt += "  ·  " + t("banner_crc", c=_fmt_int(s.crc_errors))
        n_alarm = self._alarm_notes.get(s, 0)
        if n_alarm:
            txt += "  ·  " + t("banner_alarms", n=_fmt_int(n_alarm))
            if state == "ok":
                state = "bad"
        self._log(txt, {"ok": "ok", "bad": "error"}.get(state, "info"))
        if s.finish_reason == "segment":
            self._log(t("msg_segment", n=_fmt_int(s.n)), "info")
        self._banner_base = txt
        self._unsaved.append(s)
        # Bölünen canlı kayıt ve kopan bağlantı: ekrandan gidecek veri kaybolmasın diye her zaman kaydedilir
        if self.chk_auto.isChecked() or s.finish_reason in ("segment", "lost") or s.autosave_path:
            self._save_session(s, mode="auto")
            txt += "   ·   " + t("banner_saving")
        self._show_banner(txt, state, show_open=False)

    def _show_banner(self, text: str, state: str, show_open: bool) -> None:
        self.banner_lbl.setText(text)
        self.banner.setProperty("state", state)
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)
        self.banner_open.setVisible(show_open)
        self.banner_folder.setVisible(show_open)
        self.banner.show()

    # ================================================================ İSTATİSTİK
    def _update_stats(self, force: bool = False) -> None:
        t = self.t
        self._refresh_summaries()
        s = self.view
        eng = self.engine
        n = s.n if s else 0
        if s and s.expected is not None:
            sub = t("rows_sub_expected", n=_fmt_int(s.expected))
        elif s and s.channels:
            sub = t("rows_sub_channels", n=len(s.channels))
        else:
            sub = " "
        self.tile_rows.set(_fmt_int(n), sub)

        if eng and len(self._rate) >= 2:
            (t0, l0, b0), (t1, l1, b1) = self._rate[0], self._rate[-1]
            dt = max(1e-3, t1 - t0)
            lps, kbs = (l1 - l0) / dt, (b1 - b0) / dt / 1024
            self.tile_rate.set(t("rate_fmt", lps=_fmt_int(int(round(lps)))), t("rate_sub", kbs=f"{kbs:.1f}"))
        else:
            self.tile_rate.set("—", " ")

        if s and s.integrity_known:
            m = s.missing
            bad = not s.integrity_ok
            self.tile_missing.set(_fmt_int(m), t("missing_bad_sub", g=len(s.gaps)) if bad else t("missing_ok_sub"),
                                  th.RED_TXT if bad else th.GREEN_TXT)
        else:
            self.tile_missing.set("—", t("missing_unknown_sub"))

        if eng:
            self.tile_dur.set(_fmt_dur(time.time() - self._conn_t0), " ")
        elif s and not s.imported:
            self.tile_dur.set(_fmt_dur(s.duration_s()), " ")
        else:
            self.tile_dur.set("—", " ")

        if eng:
            receiving = eng.last_rx and (time.perf_counter() - eng.last_rx) < 0.4
            if eng.reconnecting:
                self.tile_status.set(t("status_reconnecting"), " ", th.YELLOW_TXT)
            elif receiving:
                self.tile_status.set(t("status_receiving"), " ", th.BLUE_TXT)
            elif self.view is not None and self.view is not eng.current and self.view.n:
                self.tile_status.set(t("status_done"), " ", th.GREEN_TXT)
            else:
                self.tile_status.set(t("status_waiting"), " ", th.YELLOW_TXT)
            cur = eng.current
            if self._conn_info[2] == "dma" and cur.expected and cur.n < cur.expected:
                self.progress.setValue(int(cur.n * 1000 / max(1, cur.expected)))
                self.progress.setFormat(f"{_fmt_int(cur.n)} / {_fmt_int(cur.expected)}")
                self.progress.show()
            else:
                self.progress.hide()
        else:
            if s and s.imported:
                self.tile_status.set(t("status_file"), s.port, th.BLUE_TXT)
            elif s and s.n:
                self.tile_status.set(t("status_done"), " ", th.GREEN_TXT)
            else:
                self.tile_status.set(t("status_idle"), " ", th.MUTED)

    def _set_status_dot(self, on: bool) -> None:
        self.status_dot.setStyleSheet(f"color: {th.GREEN_TXT if on else th.RED_TXT}; font-size: 11pt;")

    def _update_status_text(self) -> None:
        if self.engine is not None:
            port, baud, mode = self._conn_info
            txt = f"{port} @ {baud} baud  ·  {self.t('mode_' + mode)}"
            if self._conn_trig:
                tr = self._trig
                txt += "  ·  " + (self.t("trig_status", s=int(min(self._trig_dur_s, tr.get("el", 0))), d=self._trig_dur_s)
                                  if tr else self.t("trig_waiting"))
            if self.status_lbl.text() != txt:
                self.status_lbl.setText(txt)
        else:
            self.status_lbl.setText(self.t("status_idle"))

    # ================================================================ GRAFİK
    def _invalidate_plot(self, *_):
        self._plot_sig = None

    def _x_kind(self, s: Session) -> str:
        k = self.x_cb.currentData() or "auto"
        if k == "auto":
            k = "host" if s.mode == "live" else ("time" if s.sample_rate > 0 else "sample")
        if k == "time" and s.sample_rate <= 0:
            k = "sample"
        return k

    def _update_plot(self) -> None:
        s = self.view
        if s is None:
            return
        if self._plot_collapsed:                  # grafik gizliyken hiç çizilmez (işlemci rahatlar)
            self._refresh_notes()
            return
        kind = self._x_kind(s)
        win = self.win_cb.currentData() if (s.mode == "live" and s.ended_at is None) else 0
        # Büyük veri alınırken grafiği çizim süresine göre seyrelt (arayüz akıcı kalsın)
        if s.ended_at is None and self._plot_sig is not None and self._plot_sig[0] == id(s):
            if time.perf_counter() - self._plot_t < max(0.2, 4 * self._plot_dur):
                return
        t_start = time.perf_counter()
        with s.lock:
            n = s.n
            sig = (id(s), n, kind, win, len(s.channels))
            if sig == self._plot_sig:
                return
            start = 0
            if win and n and len(s.t_host) >= n:
                start = bisect_left(s.t_host, s.t_host[n - 1] - win)
            names = list(s.channels)
            ys = [col[start:n] for col in s.cols]
            th0 = s.t_host[0] if len(s.t_host) else 0.0
            th_ = s.t_host[start:n] if len(s.t_host) >= n else None
            notes = list(s.notes)
            seq = s.seq[start:n] if (s.has_seq and len(s.seq) >= n) else None
            seq0 = s.seq[0] if seq is not None and len(s.seq) else 0
            fs = s.sample_rate
        self._plot_sig = sig

        if kind == "host":
            x = np.frombuffer(th_, dtype=np.float64) - th0 if th_ is not None and len(th_) else np.empty(0)
            xlabel = self.t("x_host")
        elif kind == "time":
            base = (np.frombuffer(seq, dtype=np.int64) - seq0) if seq is not None else np.arange(start, n)
            x = base / fs
            xlabel = self.t("x_time")
        else:
            x = np.frombuffer(seq, dtype=np.int64).astype(np.float64) if seq is not None else np.arange(start, n, dtype=np.float64)
            xlabel = self.t("x_sample")

        if self._curve_owner is not s:
            for c in self._curves.values():
                self.plot.removeItem(c)
            self._curves.clear()
            self.legend.clear()
            self._curve_owner = s
            self.plot.enableAutoRange()
            self._fft_fresh = True

        x_raw = np.asarray(x, dtype=np.float64)
        # örnekleme hızı (FFT ve imleç frekansı için)
        # Örnekleme hızı girilmemişse yalnızca canlı izlemede PC zamanından tahmin edilir. Deney
        # aktarımında PC zamanı USB aktarım hızını gösterir, örnekleme hızını değil → "örnek başına".
        t_est = None
        if fs <= 0 and s.mode == "live" and th_ is not None and len(th_) == len(x_raw) and len(x_raw) > 2:
            t_est = np.frombuffer(th_, dtype=np.float64)
        fs_eff = fs if fs > 0 else (analysis.estimate_fs(t_est) if t_est is not None else 0.0)
        # Kayıp bölgeleri: çizgiyi kopar ve kırmızı ile işaretle (ara değer uydurma)
        for r in self._gap_items:
            self.plot.removeItem(r)
        self._gap_items = []
        gap_idx = None
        if seq is not None and len(seq) > 1:
            sq = np.frombuffer(seq, dtype=np.int64)
            gap_idx = np.nonzero(np.diff(sq) > 1)[0] + 1
            for gi in gap_idx[:100]:
                if gi < len(x):
                    reg = pg.LinearRegionItem((float(x[gi - 1]), float(x[gi])), movable=False,
                                              brush=pg.mkBrush(248, 81, 73, 45), pen=pg.mkPen(248, 81, 73, 110))
                    reg.setZValue(-10)
                    self.plot.addItem(reg)
                    self._gap_items.append(reg)
            if len(gap_idx):
                x = x.astype(np.float64)
                x = np.insert(x, gap_idx, x[gap_idx - 1])   # x sıralı kalır, y'de NaN ile çizgi kopar

        conv_ys, labels = [], []
        for i, name in enumerate(names):
            u = analysis.unit_of(self._units, name) if not s.imported else None   # açılan dosya zaten dönüştürülmüş
            lbl = analysis.label(name, u)
            labels.append(lbl)
            c = self._curves.get(name)
            if c is None:
                color = th.SERIES[i % len(th.SERIES)]
                c = self.plot.plot(pen=pg.mkPen(color, width=1.6), name=html.escape(lbl), connect="finite")
                c.setDownsampling(auto=True, method="peak")
                c.setClipToView(True)
                self._curves[name] = c
            y = np.frombuffer(ys[i], dtype=np.float64) if len(ys[i]) else np.empty(0)
            y = analysis.convert(y, u)
            conv_ys.append(y)
            if gap_idx is not None and len(gap_idx) and len(y) == len(x) - len(gap_idx):
                y = np.insert(y, gap_idx, np.nan)
            m = min(len(x), len(y))
            c.setData(x[:m], y[:m])
        self.legend.setVisible(bool(self._curves))
        thr = self._draw_alarm_lines(names)
        self._apply_plot_limits(x_raw, conv_ys, thr)
        self._last_plot = {"x": x_raw, "ys": conv_ys, "names": labels, "kind": kind, "fs": fs_eff, "t": t_est,
                           "unit_x": "s" if kind in ("time", "host") else ""}
        self._draw_notes(notes, start, n, x_raw, s)
        self._plot_t = time.perf_counter()
        self._plot_dur = self._plot_t - t_start
        self.plot.getPlotItem().setLabel("bottom", xlabel)
        # canlı kayıtta analiz en fazla saniyede 2 kez (uzun sürüyorsa daha da seyrek)
        if s.ended_at is not None or \
                time.perf_counter() - self._analysis_t > max(0.5, 5 * self._analysis_dur):
            self._update_analysis()
        self._update_readout()

    # ================================================================ EŞİK ALARMLARI
    def _style_alarm_button(self) -> None:
        n = sum(1 for a in self._alarms if a["on"])
        self.btn_alarms.setText(self.t("alarms_btn") + (f"  ({n})" if n else ""))
        self.btn_alarms.setToolTip(self.t("alarms_help"))

    def _edit_alarms(self) -> None:
        chans = list(self.view.channels) if self.view is not None and not self.view.imported else []
        if self.engine is not None:
            chans = list(dict.fromkeys(chans + list(self.engine.current.channels)))
        dlg = AlarmsDialog(self, self.lang, chans, self._alarms, self._units)
        if dlg.exec():
            self._alarms = dlg.result_alarms()
            self._alarm_armed, self._alarm_fired_t, self._alarm_pending = {}, {}, {}
            if self.engine is not None:              # yeni/açılan alarm geçmiş veriye bakmasın, buradan başlasın
                cur = self.engine.current
                self._alarm_scanned[cur] = cur.n
            self.settings.setValue("alarms", json.dumps(self._alarms, ensure_ascii=False))
            self._style_alarm_button()
            self._alarm_sig = None
            self._plot_sig = None

    def _alarm_label(self, a: dict) -> str:
        u = analysis.unit_of(self._units, a["ch"])
        unit = f" {u['unit']}" if u and u.get("unit") else ""
        return f"{a['ch']} {a['op']} {analysis.fmt(a['value'])}{unit}"

    def _draw_alarm_lines(self, names) -> list[float]:
        """Grafikte eşik çizgileri (kesikli kırmızı). Döndürür: çizilen eşik değerleri."""
        act = [a for a in self._alarms if a["on"] and a["ch"] in names]
        sig = (tuple((a["ch"], a["op"], a["value"]) for a in act), th.MODE, th.ACCENT_NAME)
        if sig != self._alarm_sig:
            self._alarm_sig = sig
            for ln in self._alarm_lines:
                self.plot.removeItem(ln)
            self._alarm_lines = []
            for a in act:
                ln = pg.InfiniteLine(pos=a["value"], angle=0, movable=False,
                                     pen=pg.mkPen(th.RED_TXT, width=1.4, style=Qt.DashLine),
                                     label="⚠ " + self._alarm_label(a),
                                     labelOpts={"position": 0.97, "color": th.RED_TXT, "fill": th.BG_PANEL,
                                                "anchors": [(1, 1), (1, 1)]})
                ln.setZValue(4)
                self.plot.addItem(ln)
                self._alarm_lines.append(ln)
        return [a["value"] for a in act]

    def _check_alarms(self) -> None:
        """Gelen yeni örnekleri eşiklerle karşılaştırır (grafik gizliyken de çalışır)."""
        eng = self.engine
        if eng is not None:
            self._drain_done(eng)                    # önce biten kayıtların son satırları (sıra korunur)
            if self.engine is eng:
                self._alarm_scan_session(eng.current, live=True)
        self._flush_alarm_pending()
        self._trig_update()

    def _flush_alarm_pending(self, force: bool = False) -> None:
        """Saniye sınırı yüzünden ayrıca yazılmayan tetikleri tek satırda özetler."""
        if not self._alarm_pending:
            return
        now = time.monotonic()
        for key in list(self._alarm_pending):
            last = self._alarm_fired_t.get(key[0])
            if force or last is None or now - last[0] >= 1.0:
                n, label, val = self._alarm_pending.pop(key)
                if n:
                    self._log(self.t("msg_alarm_repeat", label=label, n=_fmt_int(n), val=analysis.fmt(val)), "error")

    def _alarm_scan_session(self, s: Session, live: bool) -> None:
        """Oturumun henüz bakılmamış satırlarını tarar. Biten oturumlar da (_drain_done) sonuna kadar
        taranır; böylece iki kontrol arasında başlayıp biten hızlı aktarımlar da kaçmaz."""
        act = [(i, a) for i, a in enumerate(self._alarms) if a["on"]]
        start = self._alarm_scanned.get(s, 0)
        with s.lock:
            n = s.n
            if not act or n <= start:
                self._alarm_scanned[s] = n              # alarm yokken de ilerle: sonra eklenen alarm geçmişe bakmaz
                return
            start = max(start, n - 2_000_000)           # çok geride kaldıysa eskisini atla
            names = list(s.channels)
            cols = {nm: s.cols[k][start:n] for k, nm in enumerate(names) if any(a["ch"] == nm for _, a in act)}
            th_ = s.t_host[start:n] if len(s.t_host) >= n else None
        self._alarm_scanned[s] = n
        for i, a in act:
            col = cols.get(a["ch"])
            if col is None or not len(col):
                continue
            y = analysis.convert(np.frombuffer(col, dtype=np.float64), analysis.unit_of(self._units, a["ch"]))
            hits, armed = analysis.alarm_scan(y, a["op"], a["value"], a["hyst"], self._alarm_armed.get(i, True))
            self._alarm_armed[i] = armed
            if hits:
                k = hits[0]
                self._fire_alarm(i, a, s, start + k, float(y[k]), th_[k] if th_ is not None else None,
                                 len(hits), live)

    def _fire_alarm(self, i: int, a: dict, s: Session, row: int, val: float, t_host, count: int,
                    live: bool = True) -> None:
        t = self.t
        now = time.monotonic()
        label = self._alarm_label(a)
        # Tetikli kayıt (saniye sınırından önce: aynı alarm kısa süre önce yazıldıysa bile tetik kaçmasın)
        trig_busy = False
        if a.get("rec") and s.ephemeral and self.engine is not None and self._conn_trig and t_host is not None:
            if self._trig is None:
                self._trig_start(label, s, row, t_host)
            else:
                trig_busy = True
        last = self._alarm_fired_t.get(i)
        if last is not None and last[1]() is s and now - last[0] < 1.0:
            pend = self._alarm_pending.setdefault((i, id(s)), [0, label, val])
            pend[0] += count                         # aynı kayıtta aynı alarm saniyede en fazla bir kez yazılır
            pend[2] = val
            return
        self._flush_alarm_pending(force=True)
        self._alarm_fired_t[i] = (now, weakref.ref(s))
        more = t("a_more", n=count - 1) if count > 1 else ""
        self._log(t("msg_alarm", label=label, val=analysis.fmt(val), row=_fmt_int(row + 1)) + more, "error")
        if trig_busy:
            self._log(t("msg_trig_busy", label=label), "muted")
        if not (self._trig is not None and self._trig.get("row_src") is s and self._trig.get("row") == row):
            self._show_banner(t("alarm_banner", label=label, val=analysis.fmt(val),
                                time=_dt.datetime.now().strftime("%H:%M:%S")), "bad", show_open=False)
        k = self._alarm_notes.get(s, 0)
        if k < ALARM_NOTE_MAX:                       # kayıt notlarla dolmasın
            with s.lock:
                s.add_note("⚠ " + t("note_alarm", label=label, val=analysis.fmt(val)),
                           t_host if t_host is not None else float(row), row=row)
            self._refresh_notes()
            self._plot_sig = None
        elif k == ALARM_NOTE_MAX:
            self._log(t("alarm_note_cap", n=ALARM_NOTE_MAX), "warn")
        self._alarm_notes[s] = k + 1
        if a["beep"]:
            QApplication.beep()
        if live and a["cmd"] and self.engine is not None and not self.engine.reconnecting:
            try:
                self.engine.send((a["cmd"] + "\r\n").encode("utf-8"))
                self._log(t("msg_sent", cmd=a["cmd"]), "info")
            except Exception as e:  # noqa: BLE001
                self._log(t("msg_send_err", err=e), "error")

    # ================================================================ TETİKLİ KAYIT
    # Sürekli akış (ephemeral) oturumlar motor bölünce/koparsa birbirine bağlanır: eski.next_session -> yeni,
    # yeni.prev_ref -> eski (zayıf). Tetikli kayıt parçaları bu zinciri izler; hangi oturumun hangi sırayla
    # bittiğini tahmin etmek gerekmez (iki kontrol arasında birden çok bölünme olsa bile satır kaçmaz).
    TRIG_GRACE = 0.5        # veri kesilse bile süre dolunca (bu pay kadar sonra) kayıt elde olanla biter

    def _trig_pre_parts(self, s: Session, start: int, t0: float) -> list:
        """Tetik, oturumun başına yakınsa önceki 'önce' saniyesini önceki oturum(lar)dan toplar."""
        if start > 0 or self._trig_pre_s <= 0:
            return []
        parts, earliest = [], None
        p = s.prev_ref() if s.prev_ref is not None else None
        with s.lock:
            if s.n and len(s.t_host) >= 1:
                earliest = s.t_host[0]
        while p is not None:                     # henüz bellekte duran önceki oturumlar (sırası bozulmaz)
            with p.lock:
                n = p.n
                if not n or len(p.t_host) < n:
                    break
                i = bisect_left(p.t_host, t0, 0, n)
                first = p.t_host[0]
            if i < n:
                parts.insert(0, (p, i, n))
                earliest = first if i == 0 else p.t_host[i]
            if i > 0 or first <= t0:
                return parts
            p = p.prev_ref() if p.prev_ref is not None else None
        tail = self._trig_tail                   # zincir koptu: bölünen eski parçaların saklanan sonu
        if tail is not None and tail.n:
            i = bisect_left(tail.t_host, t0, 0, tail.n)
            j = tail.n if earliest is None else bisect_left(tail.t_host, earliest, i, tail.n)
            if j > i:
                parts.insert(0, (tail, i, j))
        return parts

    def _trig_start(self, label: str, s: Session, row: int, t_trig: float) -> None:
        """'Kaydı başlat' işaretli alarm tetiklendi: önceki 'önce' saniyeyle birlikte kayıt başlar."""
        pre, dur = self._trig_pre_s, self._trig_dur_s
        t0 = t_trig - pre
        with s.lock:
            n = s.n
            if pre <= 0 or len(s.t_host) < n:
                start = min(row, n)
            else:
                start = min(bisect_left(s.t_host, t0, 0, n), row)
        parts = self._trig_pre_parts(s, start, t0)
        before = sum(b - a for _, a, b in parts)
        self._trig = {"parts": parts, "src": s, "start": start, "t_trig": t_trig, "t_end": t_trig + dur,
                      "label": label, "row_src": s, "row": row, "clip_row": before + row - start, "el": 0.0}
        self._log(self.t("msg_trig_start", label=label, dur=dur, pre=pre), "ok")
        self._show_banner(self.t("banner_trig", label=label, dur=dur), "info", show_open=False)
        self._update_status_text()

    def _trig_update(self) -> None:
        """Süren tetikli kaydın sonuna gelindi mi? (100 ms'de bir)"""
        tr = self._trig
        if tr is None:
            if self._conn_trig:
                self._update_status_text()
            return
        src = tr["src"]
        eng = self.engine
        if src is None:
            return
        end = None
        with src.lock:
            n = src.n
            last = src.t_host[n - 1] if n and len(src.t_host) >= n else None
            if last is not None and last >= tr["t_end"]:
                end = bisect_right(src.t_host, tr["t_end"], tr["start"], n)
            elif eng is not None and time.perf_counter() - eng.t0 >= tr["t_end"] + self.TRIG_GRACE \
                    and src.next_session is None:
                end = n                         # veri kesildi: süre doldu, elde olanla bitir
        if last is not None:
            tr["el"] = max(tr["el"], last - tr["t_trig"])
        if end is not None:
            tr["parts"].append((src, tr["start"], end))
            tr["src"] = None
            self._trig_finish(cut=False)
        else:
            self._update_status_text()

    def _trig_session_finished(self, s: Session) -> None:
        """Biten oturum: süren tetikli kayıt ondaysa elde olan kısmı al, zincirde bir sonrakine geç;
        tetik arabelleğinin son 'önce' saniyesini sakla (sınıra yakın tetikler için)."""
        tr = self._trig
        if tr is not None and tr["src"] is s:
            with s.lock:
                n = s.n
                last = s.t_host[n - 1] if n and len(s.t_host) >= n else None
                done = last is not None and last >= tr["t_end"]
                end = bisect_right(s.t_host, tr["t_end"], tr["start"], n) if done else n
            tr["parts"].append((s, tr["start"], end))
            nxt = s.next_session
            if done or nxt is None:            # süre doldu ya da akış bitti (bağlantı kesildi / hata)
                tr["src"] = None
                self._trig_finish(cut=not done and self.engine is None)
            else:
                tr["src"], tr["start"] = nxt, 0
        if s.ephemeral and self._trig_pre_s > 0 and s.next_session is not None:
            with s.lock:
                n = s.n
                ok = bool(n) and len(s.t_host) >= n
                cutoff = s.t_host[n - 1] - self._trig_pre_s if ok else 0.0
            if ok:                             # son 'önce' saniye: önceki saklanan + bu oturum (kısa bölümler için)
                parts = []
                tail = self._trig_tail
                if tail is not None and tail.n:
                    i = bisect_left(tail.t_host, cutoff, 0, tail.n)
                    if i < tail.n:
                        parts.append((tail, i, tail.n))
                with s.lock:
                    j = bisect_left(s.t_host, cutoff, 0, n)
                parts.append((s, j, n))
                self._trig_tail = extract_session(parts, reason="tail")
        elif s.ephemeral:
            self._trig_tail = None

    def _trig_finish(self, cut: bool) -> None:
        tr, self._trig = self._trig, None
        if tr is None:
            return
        parts = list(tr["parts"])
        src = tr["src"]
        if src is not None:                    # bağlantı kesildi: kaynakta ne varsa
            with src.lock:
                n = src.n
            parts.append((src, tr["start"], n))
        parts = [p for p in parts if p[2] > p[1]]
        self._update_status_text()
        if not parts:
            return
        t = self.t
        clip = extract_session(parts, reason="trigger")
        clip.name_tag = t("trig_file_tag")
        row = min(max(0, tr["clip_row"]), max(0, clip.n - 1))
        clip.add_note(t("note_trigger", label=tr["label"]), tr["t_trig"], row=row)
        clip.notes.sort(key=lambda x: x[0])
        self._trig_count += 1
        k = self._trig_count
        txt = t("msg_trig_cut" if cut else "msg_trig_done", k=k, n=_fmt_int(clip.n))
        self._log(txt, "ok")
        self._unsaved.append(clip)
        self.banner_session = clip
        self._banner_base = txt
        self._show_banner(txt + "   ·   " + t("banner_saving"), "info", show_open=False)
        self._save_session(clip, mode="auto")

    # ================================================================ KATLANABİLİR PANELLER
    def _cards(self) -> dict:
        return {"conn": self.card_conn, "data": self.card_data, "save": self.card_save}

    def _collapsed_state(self) -> dict:
        d = {k: c.is_collapsed() for k, c in self._cards().items()}
        d["plot"] = self._plot_collapsed
        return d

    def _save_collapsed(self, *_) -> None:
        self.settings.setValue("collapsed", json.dumps(self._collapsed_state()))

    def _set_plot_collapsed(self, on: bool, save: bool = True) -> None:
        """Grafik paneli gizlenince sadece başlığı kalır; boşalan yeri terminal alır."""
        on = bool(on)
        if on == self._plot_collapsed:
            return
        if on:
            self._split_open = self.splitter.sizes()
        self._plot_collapsed = on
        for w in (self.plot_tools, self.plot_split, self.lbl_x, self.x_cb, self.lbl_win, self.win_cb,
                  self.btn_fit, self.btn_png):
            w.setVisible(not on)
        self.btn_plot_toggle.setText("▸" if on else "▾")
        self.btn_plot_toggle.setToolTip(self.t("plot_show" if on else "plot_hide"))
        if on:
            hh = self.plot_hdr.sizeHint().height() + 26
            self.plot_panel.setMaximumHeight(hh)
            total = sum(self.splitter.sizes())
            self.splitter.setSizes([hh, max(100, total - hh)])
        else:
            self.plot_panel.setMaximumHeight(16777215)
            sizes = getattr(self, "_split_open", None)
            if sizes and sum(sizes) > 0 and sizes[0] > 120:
                self.splitter.setSizes(sizes)
            else:
                total = sum(self.splitter.sizes())
                self.splitter.setSizes([int(total * .62), int(total * .38)])
            self._plot_sig = None
            QTimer.singleShot(0, self._update_plot)
        if self.tabs.currentIndex() == 1:
            self._update_analysis()
        if save:
            self._save_collapsed()

    def _refresh_summaries(self) -> None:
        """Katlı kartların başlığındaki kısa özet."""
        t = self.t
        mode = t("mode_live_short") if self.seg_mode.value() == "live" else t("mode_dma_short")
        port = self.port_cb.currentData() or "—"
        fmt_ = t("fmt_bin_short") if self._data_format == "binary" else ""
        self.card_conn.set_summary(" · ".join(x for x in (str(port), self.baud_cb.currentText(), mode, fmt_) if x))
        fs = self.fs_spin.value()
        self.card_data.set_summary(" · ".join(x for x in (
            f"{analysis.fmt(fs)} Hz" if fs > 0 else t("fs_unknown_short"),
            t("units_n", n=len(self._units)) if self._units else "",
            t("alarms_n", n=sum(1 for a in self._alarms if a["on"])) if any(a["on"] for a in self._alarms) else "") if x))
        every = self.every_cb.currentData() or 0
        auto = (t("auto_every", m=every) if every else t("auto_end_only")) if self.chk_auto.isChecked() else t("auto_off_short")
        fmt_name = {"xlsx": "Excel", "csv": "CSV", "txt": "TXT"}[self.seg_fmt.value()]
        trig = (" · " + t("trig_short")) if self.chk_trig.isChecked() and self.seg_mode.value() == "live" else ""
        self.card_save.set_summary(f"{fmt_name} · {auto}{trig}")

    @staticmethod
    def _bounds(x, ys):
        """Verinin kapladığı alan: (x0, x1, y0, y1) ya da None."""
        if not len(x):
            return None
        with np.errstate(invalid="ignore"):
            x0, x1 = float(np.fmin.reduce(x)), float(np.fmax.reduce(x))
            lo, hi = math.inf, -math.inf
            for y in ys:
                if len(y):
                    a, b = float(np.fmin.reduce(y)), float(np.fmax.reduce(y))
                    if a == a:
                        lo, hi = min(lo, a), max(hi, b)
        if not (math.isfinite(x0) and math.isfinite(x1) and math.isfinite(lo) and math.isfinite(hi)):
            return None
        return x0, x1, lo, hi

    def _apply_plot_limits(self, x, ys, extra_y=()) -> None:
        """Sürükleme/yakınlaştırma verinin dışına taşmasın: görünüm, verinin biraz ötesinde durur;
        en fazla tüm veri ekrana sığana kadar uzaklaşılabilir."""
        pi = self.plot.getPlotItem()
        vb = pi.getViewBox()
        b = self._bounds(x, ys)
        if pi.ctrl.logXCheck.isChecked() or pi.ctrl.logYCheck.isChecked():
            b = None                              # log eksende sınır birimi farklı: sınır koyma
        if b is None:
            vb.setLimits(xMin=-1e307, xMax=1e307, yMin=-1e307, yMax=1e307, maxXRange=None, maxYRange=None)
            return
        x0, x1, y0, y1 = b
        for v in extra_y:                       # alarm eşik çizgileri de görülebilsin
            y0, y1 = min(y0, v), max(y1, v)
        sx = (x1 - x0) or 1.0
        sy = (y1 - y0) or max(abs(y1) * 0.1, 1.0)
        px, py = sx * 0.04, sy * 0.12
        vb.setLimits(xMin=x0 - px, xMax=x1 + px, yMin=y0 - py, yMax=y1 + py,
                     maxXRange=sx + 2 * px, maxYRange=sy + 2 * py)
        lines = self._cursor_lines
        v = self.view
        ctx = (self._x_kind(v), id(v)) if v is not None else None
        if v is not None and v.mode == "dma" and v.ended_at is None:
            ctx = self._cursor_ctx                  # aktarım sürüyor: bitince (tam veriyle) karar ver
        if ctx != self._cursor_ctx:                 # sadece eksen türü / kayıt değişince (canlı akışta zıplamasın)
            self._cursor_ctx = ctx
            if lines and any(not (x0 - px <= ln.value() <= x1 + px) for ln in lines):
                for k, ln in enumerate(lines):      # imleçler ulaşılabilir yere gelsin
                    ln.setValue(x0 + sx * (0.33 if k == 0 else 0.66))

    def _fit_plot(self) -> None:
        self.plot.enableAutoRange()
        if self.fft_plot.isVisible():
            self._fft_fresh = True
            self.fft_plot.enableAutoRange()
            self._update_analysis()

    # ================================================================ ANALİZ
    def _visible_slice(self):
        """Grafikte görünen x aralığına düşen örnekler (yoksa tümü)."""
        lp = self._last_plot
        if not lp or not len(lp["x"]):
            return None, 0, 0
        x0, x1 = self.plot.getPlotItem().getViewBox().viewRange()[0]
        x = lp["x"]
        i0 = int(np.searchsorted(x, x0, side="left"))
        i1 = int(np.searchsorted(x, x1, side="right"))
        if i1 - i0 < 2:
            i0, i1 = 0, len(x)
        return lp, i0, i1

    def _on_tab_changed(self, idx: int) -> None:
        self.tabs.cornerWidget(Qt.TopRightCorner).setVisible(idx == 0)
        if idx == 1:
            self._update_analysis()

    def _update_analysis(self) -> None:
        """İstatistik tablosu ve FFT (görünen aralık üzerinden)."""
        t0 = self._analysis_t = time.perf_counter()
        try:
            self._do_analysis()
        finally:
            self._analysis_dur = time.perf_counter() - t0
            # kaydırırken çok sık hesaplamasın: süre uzunsa bekleme de uzar
            self._analysis_timer.setInterval(int(min(1500, max(150, 2000 * self._analysis_dur))))

    def _do_analysis(self) -> None:
        if self._plot_collapsed:                  # istatistik grafikteki görünen aralıktan hesaplanır
            self.stats_table.setRowCount(0)
            self.lbl_stats_scope.setText(self.t("stats_plot_hidden"))
            return
        want_stats = self.tabs.currentIndex() == 1
        want_fft = self.btn_fft.isChecked()
        if not (want_stats or want_fft):
            return
        lp, i0, i1 = self._visible_slice()
        if lp is None:
            self.stats_table.setRowCount(0)
            self.lbl_stats_scope.setText("")
            for c in self._fft_curves.values():
                c.setData([], [])
            return
        fs = lp["fs"]
        if lp["t"] is not None:                # tahmini hız: görünen aralığa göre
            fs = analysis.estimate_fs(lp["t"][i0:i1]) or fs
        self._peaks = {}
        if want_fft:
            if set(self._fft_curves) != set(lp["names"]):
                for c in self._fft_curves.values():
                    self.fft_plot.removeItem(c)
                self._fft_curves = {}
                for i, name in enumerate(lp["names"]):
                    c = self._fft_curves[name] = self.fft_plot.plot(
                        pen=pg.mkPen(th.SERIES[i % len(th.SERIES)], width=1.4), name=html.escape(name))
                    c.setDownsampling(auto=True, method="peak")
                    c.setClipToView(True)
            fmax = 0.0
            for name, y in zip(lp["names"], lp["ys"]):
                res = analysis.spectrum(y[i0:i1], fs)
                if res is None:
                    self._fft_curves[name].setData([], [])
                    continue
                f, a, peak, _amp = res
                self._fft_curves[name].setData(f, a)
                self._peaks[name] = peak
                fmax = max(fmax, analysis.useful_fmax(f, a))
            self._apply_fft_limits()
            if self._fft_fresh and fmax > 0:     # ilk açılışta ilginç bölgeye yakınlaş (sonra kullanıcıya bırak)
                self._fft_fresh = False
                self.fft_plot.enableAutoRange(axis="y")
                self.fft_plot.setXRange(0, fmax, padding=0.02)
            self.fft_plot.getPlotItem().setLabel("bottom", self.t("fft_x") if fs > 0 else self.t("fft_x_samples"))
        if want_stats:
            names = lp["names"]
            self.stats_table.setRowCount(len(names))
            for r, (name, y) in enumerate(zip(names, lp["ys"])):
                st = analysis.stats(y[i0:i1])
                peak = self._peaks.get(name)
                cells = [name] + (["—"] * 6 if st is None else
                                  [analysis.fmt(st[k]) for k in ("last", "min", "max", "mean", "rms", "std")])
                cells.append("—" if peak is None else f"{peak:.4g}" + (" Hz" if fs > 0 else ""))
                for c, txt in enumerate(cells):
                    it = self.stats_table.item(r, c)
                    if it is None:
                        it = QTableWidgetItem()
                        self.stats_table.setItem(r, c, it)
                    it.setText(txt)
            self.lbl_stats_scope.setText(self.t("stats_scope", n=_fmt_int(i1 - i0))
                                         + ("" if want_fft else "  ·  " + self.t("stats_fft_hint")))

    def _apply_fft_limits(self) -> None:
        fx, fy = 0.0, 0.0
        for c in self._fft_curves.values():
            xd, yd = c.getData()
            if xd is not None and len(xd):
                fx = max(fx, float(xd[-1]))
                fy = max(fy, float(np.nanmax(yd)) if len(yd) else 0.0)
        fpi = self.fft_plot.getPlotItem()
        vb = fpi.getViewBox()
        if fx <= 0 or fpi.ctrl.logXCheck.isChecked() or fpi.ctrl.logYCheck.isChecked():
            vb.setLimits(xMin=-1e307, xMax=1e307, yMin=-1e307, yMax=1e307, maxXRange=None, maxYRange=None)
            return
        fy = fy if fy > 0 and math.isfinite(fy) else 1.0
        vb.setLimits(xMin=0, xMax=fx * 1.02, yMin=-fy * 0.05, yMax=fy * 1.25, maxXRange=fx * 1.02, maxYRange=fy * 1.3)

    def _toggle_fft(self, on: bool) -> None:
        self.fft_plot.setVisible(on)
        if on:
            self._fft_fresh = True
            self.fft_plot.enableAutoRange()
            self._update_analysis()

    def _toggle_cursors(self, on: bool) -> None:
        for ln in self._cursor_lines:
            self.plot.removeItem(ln)
        self._cursor_lines = []
        if on:
            x0, x1 = self.plot.getPlotItem().getViewBox().viewRange()[0]
            for k, (frac, name) in enumerate(((1 / 3, "A"), (2 / 3, "B"))):
                color = th.ACCENT_TXT if k == 0 else "#e3b341"
                ln = pg.InfiniteLine(pos=x0 + (x1 - x0) * frac, angle=90, movable=True,
                                     pen=pg.mkPen(color, width=1.5, style=Qt.DashLine),
                                     hoverPen=pg.mkPen(color, width=3), label=name,
                                     labelOpts={"position": 0.96, "color": color, "fill": th.BG_PANEL})
                ln.sigPositionChanged.connect(lambda *_: self._update_readout())
                self.plot.addItem(ln)
                self._cursor_lines.append(ln)
        self._update_readout()

    def _update_readout(self) -> None:
        """İmleç değerleri: A, B, aralarındaki fark ve frekans."""
        if not self._cursor_lines:
            self.lbl_readout.setText(self.t("cursor_hint") if self.btn_cursor.isChecked() else "")
            self.lbl_readout.setToolTip("")
            return
        lp = self._last_plot
        a, b = (ln.value() for ln in self._cursor_lines)
        ux = lp["unit_x"] if lp else ""
        dx = b - a
        parts = [f"A = {analysis.fmt(a)} {ux}".strip(), f"B = {analysis.fmt(b)} {ux}".strip(),
                 f"Δ = {analysis.fmt(dx)} {ux}".strip()]
        if lp and dx:
            if ux == "s":
                parts.append(f"1/Δ = {analysis.fmt(1 / abs(dx))} Hz")
            elif lp["fs"] > 0:
                parts.append(f"Δt = {analysis.fmt(abs(dx) / lp['fs'])} s")
        lines = []
        if lp and len(lp["x"]):
            ia, ib = analysis.nearest(lp["x"], a), analysis.nearest(lp["x"], b)
            for name, y in zip(lp["names"], lp["ys"]):
                if ia < len(y) and ib < len(y):
                    ya, yb = float(y[ia]), float(y[ib])
                    lines.append(f"{name}: {analysis.fmt(ya)} → {analysis.fmt(yb)} (Δ {analysis.fmt(yb - ya)})")
        text = "   ".join(parts)
        if lines:
            text += "   |   " + "   ".join(lines[:2]) + ("   …" if len(lines) > 2 else "")
        self.lbl_readout.setText(text)
        self.lbl_readout.setToolTip(_tip("\n".join(["   ".join(parts)] + lines)))

    # ================================================================ NOTLAR
    def _note_target(self) -> Session | None:
        eng = self.engine
        if eng is not None and eng.current.n > 0:
            return eng.current
        return self.view if (self.view is not None and self.view.n > 0) else None

    def _add_note(self) -> None:
        s = self._note_target()
        if s is None:
            self._msg("info", APP_NAME, self.t("msg_nodata"))
            return
        # Not, kısayola basıldığı ANA bağlanır (yazarken gelen satırlara değil)
        eng = self.engine
        with s.lock:
            row = max(0, s.n - 1)
            if eng is not None and s is eng.current:
                t_now = time.perf_counter() - eng.t0
            else:
                t_now = s.t_host[row] if len(s.t_host) > row else 0.0
        text, ok = QInputDialog.getText(self, self.t("note_title"), self.t("note_ask"))
        text = clean_note(text)
        if not ok or not text:
            return
        with s.lock:
            row = s.add_note(text, t_now, row=row)
        self._log(self.t("msg_note", row=_fmt_int(row + 1), text=text), "info")
        self._plot_sig = None
        self._refresh_notes()
        if s.ended_at is not None:
            if s not in self._unsaved:
                self._unsaved.append(s)             # kaydedilemezse kapatırken sorulsun
            if s.saved_path or s.exporting or s.autosave_path:
                self._save_session(s, mode="auto")  # kaydedilmiş dosyaya da yansısın (yazma sürüyorsa sıraya girer)

    def _draw_notes(self, notes, start: int, n: int, x_raw, s: Session) -> None:
        """Not çizgileri: her karede yeniden oluşturmak yerine sadece yerleri güncellenir."""
        if self._note_owner is not s:
            self._clear_note_items()
            self._note_owner = s
        want = {}
        for idx in range(max(0, len(notes) - 200), len(notes)):
            r = min(notes[idx][0], n - 1)
            if start <= r and r - start < len(x_raw):
                want[(idx, notes[idx][0], notes[idx][2])] = float(x_raw[r - start])
        for idx in [k for k in self._note_items if k not in want]:
            self.plot.removeItem(self._note_items.pop(idx))
        for idx, pos in want.items():
            ln = self._note_items.get(idx)
            if ln is None:
                ln = pg.InfiniteLine(pos=pos, angle=90, movable=False,
                                     pen=pg.mkPen("#e3b341", width=1.2, style=Qt.DotLine),
                                     label="📝 " + idx[2][:40],
                                     labelOpts={"position": 0.88, "color": "#e3b341", "fill": th.BG_PANEL})
                ln.setZValue(5)
                self.plot.addItem(ln)
                self._note_items[idx] = ln
            elif ln.value() != pos:
                ln.setValue(pos)
        self._refresh_notes()

    def _clear_note_items(self) -> None:
        for it in self._note_items.values():
            self.plot.removeItem(it)
        self._note_items = {}

    def _refresh_notes(self) -> None:
        s = self.view
        sig = (id(s), len(s.notes)) if s is not None else None
        if sig == self._notes_sig:
            return
        old = self._notes_sig
        self._notes_sig = sig
        notes = list(s.notes) if s is not None else []
        if old is not None and sig is not None and old[0] == sig[0] and old[1] < sig[1]:
            new = notes[old[1]:]                   # notlar sadece eklenir: tüm listeyi yeniden kurma
        else:
            self.notes_list.clear()
            new = notes
        for row, t_, text in new:
            self.notes_list.addItem(f"#{row + 1}  ·  {analysis.fmt(round(t_, 2))} s  ·  {text}")
        self.lbl_notes_empty.setVisible(not notes)

    # ================================================================ BİRİMLER
    def _edit_units(self) -> None:
        chans = list(self.view.channels) if self.view is not None and not self.view.imported else []
        if self.engine is not None:
            chans = list(dict.fromkeys(chans + list(self.engine.current.channels)))
        dlg = UnitsDialog(self, self.lang, chans, self._units)
        if dlg.exec():
            self._units = dlg.result_units()
            self.settings.setValue("units", json.dumps(self._units, ensure_ascii=False))
            self._curve_owner = None          # gösterge adları birimle yeniden yazılsın
            self._plot_sig = None
            self._alarm_sig = None            # eşik çizgisindeki birim de güncellensin
            self._fft_curves_reset()

    def _fft_curves_reset(self) -> None:
        for c in self._fft_curves.values():
            self.fft_plot.removeItem(c)
        self._fft_curves = {}

    # ================================================================ HIZLI KOMUTLAR
    def _rebuild_quick(self) -> None:
        if not hasattr(self, "qc_row"):
            return
        self._stop_repeats()
        while self.qc_row.count():
            w = self.qc_row.takeAt(0).widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self._qc_buttons = []
        for i, c in enumerate(self._qcmds):
            b = QPushButton(c["label"])
            b.setObjectName("Quick")
            b.setCheckable(c["repeat"] > 0)
            b.setToolTip(self.t("qc_tip_rep", s=analysis.fmt(c["repeat"]), cmd=c["cmd"]) if c["repeat"] > 0
                         else self.t("qc_tip_once", cmd=c["cmd"]))
            b.clicked.connect(lambda checked=False, i=i: self._quick_clicked(i, checked))
            b.setContextMenuPolicy(Qt.CustomContextMenu)
            b.customContextMenuRequested.connect(lambda pos, i=i, b=b: self._quick_menu(i, b, pos))
            b.setEnabled(self.engine is not None)
            self.qc_row.addWidget(b)
            self._qc_buttons.append(b)
        add = self._qc_add = QPushButton(self.t("qc_add"))
        add.setObjectName("Quick")
        add.setToolTip(self.t("qc_add_tip"))
        add.clicked.connect(lambda: self._quick_edit(None))
        add.setEnabled(len(self._qcmds) < 12)
        self.qc_row.addWidget(add)
        self.qc_row.addStretch(1)

    def _quick_bytes(self, c: dict) -> tuple[bytes, str] | None:
        if c["hex"]:
            try:
                data = parse_hex(c["cmd"])
            except ValueError as e:
                self._log(self.t("msg_hex_err", err=e), "error")
                return None
            return data, data.hex(" ").upper() or "∅"
        return (c["cmd"] + c["eol"]).encode("utf-8"), c["cmd"] or "⏎"

    def _quick_clicked(self, i: int, checked: bool) -> None:
        if i >= len(self._qcmds) or self.engine is None:
            return
        c = self._qcmds[i]
        if c["repeat"] > 0:
            if checked:
                tm = QTimer(self, interval=int(c["repeat"] * 1000))
                tm.timeout.connect(lambda i=i: self._quick_send(i, quiet=True))
                tm.start()
                self._qc_timers[i] = tm
                self._log(self.t("qc_repeat_on", label=c["label"], s=analysis.fmt(c["repeat"])), "info")
                self._quick_send(i)
            else:
                self._stop_repeat(i)
                self._log(self.t("qc_repeat_off", label=c["label"]), "muted")
        else:
            self._quick_send(i)

    def _quick_send(self, i: int, quiet: bool = False) -> None:
        eng = self.engine
        if eng is None or i >= len(self._qcmds):
            return
        if eng.reconnecting:                 # port geri gelene kadar tekrarlar sessizce atlanır
            if not quiet:
                self._log(self.t("msg_send_err", err=self.t("status_reconnecting")), "error")
            return
        res = self._quick_bytes(self._qcmds[i])
        if res is None:
            self._stop_repeat(i, uncheck=True)
            return
        data, shown = res
        try:
            eng.send(data)
            if not quiet:
                self._log(self.t("msg_sent", cmd=shown), "info")
            self._qc_fail.pop(i, None)
        except Exception as e:  # noqa: BLE001
            fails = self._qc_fail[i] = self._qc_fail.get(i, 0) + 1
            if fails == 1 or i not in self._qc_timers:
                self._log(self.t("msg_send_err", err=e), "error")
            # kablo çekilince ilk deneme hata verebilir (yeniden bağlanma birazdan başlar): 3 kez üst üste
            # hata olursa tekrarı durdur, terminal hatayla dolmasın
            if i in self._qc_timers and fails >= 3:
                self._stop_repeat(i, uncheck=True)
                self._log(self.t("qc_repeat_off", label=self._qcmds[i]["label"]), "muted")

    def _stop_repeat(self, i: int, uncheck: bool = False) -> None:
        self._qc_fail.pop(i, None)
        tm = self._qc_timers.pop(i, None)
        if tm is not None:
            tm.stop()
            tm.deleteLater()
        if uncheck and i < len(self._qc_buttons) and self._qc_buttons[i].isCheckable():
            b = self._qc_buttons[i]
            b.blockSignals(True)
            b.setChecked(False)
            b.blockSignals(False)

    def _stop_repeats(self) -> None:
        self._qc_fail = {}
        for tm in self._qc_timers.values():
            tm.stop()
            tm.deleteLater()
        self._qc_timers = {}
        for b in getattr(self, "_qc_buttons", []):
            if b.isCheckable():
                b.blockSignals(True)
                b.setChecked(False)
                b.blockSignals(False)

    def _quick_menu(self, i: int, btn, pos) -> None:
        m = QMenu(self)
        a_edit = m.addAction(self.t("qc_edit"))
        a_del = m.addAction(self.t("qc_delete"))
        act = m.exec(btn.mapToGlobal(pos))
        if act is a_edit:
            self._quick_edit(i)
        elif act is a_del and i < len(self._qcmds):
            del self._qcmds[i]
            self.settings.setValue("quick_cmds", json.dumps(self._qcmds, ensure_ascii=False))
            self._rebuild_quick()

    def _quick_edit(self, i: int | None) -> None:
        dlg = QuickCmdDialog(self, self.lang, self._qcmds[i] if i is not None else None)
        if not dlg.exec():
            return
        c = dlg.result_cmd()
        if i is None:
            self._qcmds.append(c)
        else:
            self._qcmds[i] = c
        self._qcmds = sanitize_cmds(self._qcmds)
        self.settings.setValue("quick_cmds", json.dumps(self._qcmds, ensure_ascii=False))
        self._rebuild_quick()

    def _save_png(self) -> None:
        from pyqtgraph.exporters import ImageExporter

        folder = self.folder_edit.text().strip() or self._default_folder()
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError:
            folder = str(Path.home())
        name = f"grafik_{_dt.datetime.now():%Y-%m-%d_%H-%M-%S}.png"
        path, _ = QFileDialog.getSaveFileName(self, self.t("save_png"), os.path.join(folder, name), "PNG (*.png)")
        if not path:
            return
        ex = ImageExporter(self.plot.getPlotItem())
        ex.parameters()["width"] = 1920
        ex.export(path)
        self.last_saved = path
        self._log(self.t("msg_png", path=path), "ok")

    # ================================================================ KAYIT
    def _unique(self, path: str) -> str:
        def taken(p):
            return os.path.exists(p) or os.path.normcase(p) in self._reserved

        if not taken(path):
            return path
        base, ext = os.path.splitext(path)
        i = 2
        while taken(f"{base}_{i}{ext}"):
            i += 1
        return f"{base}_{i}{ext}"

    def _save_session(self, s: Session, mode: str = "auto", fmt: str | None = None) -> None:
        """Kaydı her zaman AYNI dosyaya yazar (ilk seferde dosya adı belirlenir).

        mode: "user" (kullanıcı istedi: hata penceresi), "auto" (bitince), "periodic" (aralıklı, sessiz)
        """
        fmt = fmt or self.seg_fmt.value()
        if s.exporting:                    # önceki yazma sürüyor: bitince en güncel haliyle tekrar yaz
            prio = {"periodic": 0, "auto": 1, "user": 2}
            old = s.resave_pending
            if old is None or prio[mode] >= prio[old[0]]:
                s.resave_pending = (mode, fmt)
            return
        folder = self.folder_edit.text().strip() or self._default_folder()
        if s.autosave_user and s.autosave_path:   # kullanıcının "Farklı kaydet" ile seçtiği dosya
            fmt = s.autosave_fmt
        path = s.autosave_path if (s.autosave_path and s.autosave_fmt == fmt) else None
        if path and not s.autosave_user and os.path.normcase(os.path.dirname(os.path.abspath(path))) != \
                os.path.normcase(os.path.abspath(folder)):
            path = None                    # kullanıcı kayıt klasörünü değiştirdi: yeni klasöre yaz
        if path is None:
            path = self._unique(os.path.join(folder, default_filename(s, self.prefix_edit.text(), fmt)))
            s.autosave_path, s.autosave_fmt = path, fmt
        self._export_async(s, path, fmt, mode=mode)

    def _save_now(self) -> None:
        s = self.view
        if s is None or s.n == 0:
            self._msg("info", APP_NAME, self.t("msg_nodata"))
            return
        self._save_session(s, mode="user")

    def _periodic(self) -> None:
        """Uzun testler için: aralıklı otomatik kayıt ve disk kontrolü (15 sn'de bir çalışır)."""
        now = time.monotonic()
        if now - self._last_retry >= 60:   # kaydedilemeyenleri (disk dolu, dosya Excel'de açık…) yeniden dene
            self._last_retry = now
            for s in list(self._unsaved):
                if s.save_failed and s.unsaved() and not s.exporting:
                    self._save_session(s, mode="periodic")
        eng = self.engine
        if eng is None:
            return
        if now - self._last_disk >= 60:
            self._last_disk = now
            self._check_disk(self.folder_edit.text().strip() or self._default_folder(), periodic=True)
        minutes = self.every_cb.currentData() or 0
        if not (self.chk_auto.isChecked() and minutes and self._conn_info[2] == "live"):
            return
        if now - self._last_autosave < minutes * 60:
            return
        self._last_autosave = now
        cur = eng.current
        if cur.unsaved():
            self._save_session(cur, mode="periodic")

    def _export_async(self, s: Session, path: str, fmt: str, mode: str = "user") -> None:
        lang = self.lang
        # Bu işe özel kopya (aynı anda başka bir kayıt farklı birimle yazıyor olabilir).
        # Açılan dosyanın değerleri zaten dönüştürülmüş olabilir: tekrar dönüştürülmez.
        units = {} if s.imported else {k: dict(v) for k, v in self._units.items()}
        self._reserved.add(os.path.normcase(path))
        s.exporting += 1

        def job():
            info = {}
            try:
                export_session(s, path, fmt, lang, info=info, units=units)
                self._export_q.put(("ok", (path, info.get("rows", 0), info.get("notes", 0), mode, fmt), s, path))
            except Exception as e:  # noqa: BLE001
                self._export_q.put(("err", (f"{os.path.basename(path)}: {e}", mode, fmt), s, path))

        th_ = threading.Thread(target=job, name="export", daemon=True)
        self._export_threads = [x for x in self._export_threads if x.is_alive()] + [th_]
        th_.start()

    def _drain_exports(self) -> None:
        try:
            while True:
                kind, val, s, path = self._export_q.get_nowait()
                self._reserved.discard(os.path.normcase(path))
                s.exporting = max(0, s.exporting - 1)
                if kind == "ok":
                    val, rows, n_notes, mode, fmt = val
                    s.saved_path = val
                    s.saved_n = max(s.saved_n, rows)
                    s.saved_notes = max(s.saved_notes, n_notes)
                    s.save_failed = False
                    if not s.unsaved() and s in self._unsaved:
                        self._unsaved.remove(s)
                    if s.resave_pending:
                        pmode, pfmt = s.resave_pending
                        s.resave_pending = None
                        self._save_session(s, mode=pmode, fmt=pfmt)
                    self.last_saved = val
                    self.saved_btn.setText("💾 " + os.path.basename(val))
                    self.saved_btn.setToolTip(_tip(val))
                    self.saved_btn.show()
                    if mode == "periodic":    # terminali her 5 dk'da bir doldurmasın
                        self.statusBar().showMessage(self.t("msg_autosaved", name=os.path.basename(val),
                                                            n=_fmt_int(rows)), 8000)
                    else:
                        self._log(self.t("msg_saved", path=val), "ok")
                    if s is self.banner_session and self.banner.isVisible():
                        base = getattr(self, "_banner_base", "")
                        state = self.banner.property("state") or "info"
                        self._show_banner(f"{base}   ·   {self.t('banner_saved', name=os.path.basename(val))}",
                                          state, show_open=True)
                else:
                    err, mode, fmt = val
                    # Aynı dosyada ısrar etme (Excel'de açık olabilir, disk dolu olabilir): sonraki kayıt yeni ada
                    s.autosave_path = None
                    s.autosave_user = False
                    s.save_failed = True
                    if s not in self._unsaved and s.unsaved():
                        self._unsaved.append(s)
                    if s.resave_pending:
                        pmode, pfmt = s.resave_pending
                        s.resave_pending = None
                        if pmode == "user":
                            self._save_session(s, mode=pmode, fmt=pfmt)
                    self._log(self.t("msg_save_err", err=err), "error")
                    if mode == "user":
                        self._msg("error", self.t("err_title"), self.t("msg_save_err", err=err))
                    else:   # otomatik kayıt: test sürerken üst üste pencere açılmasın, bant yeter
                        self._show_banner(self.t("msg_autosave_err", err=err), "bad", show_open=False)
        except queue.Empty:
            pass

    def _save_as(self) -> None:
        s = self.view
        if s is None or s.n == 0:
            self._msg("info", APP_NAME, self.t("msg_nodata"))
            return
        fmt = self.seg_fmt.value()
        folder = self.folder_edit.text().strip() or self._default_folder()
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError:
            folder = self._default_folder()
        filters = {"xlsx": "Excel (*.xlsx)", "csv": "CSV (*.csv)", "txt": "TXT (*.txt)"}
        order = [fmt] + [k for k in filters if k != fmt]
        path, chosen = QFileDialog.getSaveFileName(
            self, self.t("save_as"), os.path.join(folder, default_filename(s, self.prefix_edit.text(), fmt)),
            ";;".join(filters[k] for k in order))
        if not path:
            return
        ext = os.path.splitext(path)[1].lower().lstrip(".")
        if ext not in FORMATS:
            ext = next((k for k, f in filters.items() if f == chosen), fmt)
            path += FORMATS[ext]
        if os.path.normcase(path) in self._reserved:    # aynı dosya şu an otomatik kaydediliyor
            path = self._unique(path)
        if s.ended_at is not None:       # bitmiş kayıt: sonradan eklenen notlar da BU dosyaya yazılsın
            s.autosave_path, s.autosave_fmt, s.autosave_user = path, ext, True
        self._export_async(s, path, ext)

    def _browse_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, self.t("choose_folder"),
                                             self.folder_edit.text() or self._default_folder())
        if d:
            self.folder_edit.setText(d)

    def _open_folder(self) -> None:
        folder = self.folder_edit.text().strip() or self._default_folder()
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as e:
            self._log(str(e), "error")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def _open_path(self, path: str | None) -> None:
        if path and os.path.exists(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _open_saved_file(self) -> None:
        from ..importer import load_file

        if self.engine is not None or not self._confirm_discard():
            return
        folder = self.folder_edit.text().strip() or self._default_folder()
        path, _ = QFileDialog.getOpenFileName(self, self.t("open_title"), folder, self.t("file_filter"))
        if not path:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            s = load_file(path, channel_prefix=self.t("channel_prefix"))
        except Exception as e:  # noqa: BLE001
            QApplication.restoreOverrideCursor()
            msg = {"no-data": self.t("msg_nodata"), "too-big": self.t("file_too_big")}.get(str(e), str(e))
            self._log(self.t("msg_load_err", err=msg), "error")
            self._msg("warn", self.t("err_title"), self.t("msg_load_err", err=msg))
            return
        QApplication.restoreOverrideCursor()
        self.view = s
        self._plot_sig = None
        self.banner.hide()
        if s.sample_rate > 0 and self.fs_spin.value() == 0:
            self.fs_spin.setValue(s.sample_rate)
        self._log(self.t("msg_loaded", path=path, n=_fmt_int(s.n)), "ok")
        self._update_stats(force=True)

    # ================================================================ TERMİNAL
    def _send(self) -> None:
        if self.engine is None:
            return
        cmd = self.send_edit.text()
        if self.chk_hex_send.isChecked():
            try:
                data = bytes.fromhex(cmd.replace("0x", "").replace("0X", "").replace(",", " "))
            except ValueError as e:
                self._log(self.t("msg_hex_err", err=e), "error")
                return
            shown = data.hex(" ").upper() or "∅"
        else:
            data = (cmd + (self.eol_cb.currentData() or "")).encode("utf-8")
            shown = cmd or "⏎"
        try:
            self.engine.send(data)
            self._log(self.t("msg_sent", cmd=shown), "info")
            if cmd and (not self._history or self._history[-1] != cmd):
                self._history.append(cmd)
                del self._history[:-HISTORY_MAX]
            self._hist_pos = len(self._history)
            self.send_edit.clear()
        except Exception as e:  # noqa: BLE001
            self._log(self.t("msg_send_err", err=e), "error")

    def eventFilter(self, obj, ev) -> bool:
        hdr = getattr(self, "plot_hdr", None)        # pencere kurulurken de olay gelebilir
        if hdr is not None and obj is hdr.title and ev.type() == QEvent.MouseButtonRelease and ev.button() == Qt.LeftButton:
            self._set_plot_collapsed(not self._plot_collapsed)
            return True
        if obj is getattr(self, "send_edit", None) and ev.type() == QEvent.KeyPress and ev.key() in (Qt.Key_Up, Qt.Key_Down):
            if self._history:
                step = -1 if ev.key() == Qt.Key_Up else 1
                self._hist_pos = max(0, min(len(self._history), self._hist_pos + step))
                self.send_edit.setText(self._history[self._hist_pos] if self._hist_pos < len(self._history) else "")
            return True
        return super().eventFilter(obj, ev)

    def _log(self, text: str, level: str = "info", stamp: bool = True) -> None:
        color = {"info": th.BLUE_TXT, "ok": th.GREEN_TXT, "warn": th.YELLOW_TXT,
                 "error": th.RED_TXT, "muted": th.FAINT}.get(level, th.MUTED)
        ts = f"[{_dt.datetime.now():%H:%M:%S}] " if stamp else ""
        sb = self.term.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 4
        self.term.appendHtml(f'<span style="color:{th.FAINT}">{ts}</span>'
                             f'<span style="color:{color}">{html.escape(str(text))}</span>')
        cur = self.term.textCursor()
        cur.movePosition(QTextCursor.End)
        cur.setCharFormat(QTextCharFormat())
        self.term.setTextCursor(cur)
        if at_bottom:
            sb.setValue(sb.maximum())
        if level in ("ok", "error", "warn"):
            self.statusBar().showMessage(str(text), 6000)

    # ================================================================ MESAJ KUTULARI
    def _msg(self, kind: str, title: str, text: str) -> None:
        """Düz metin mesaj kutusu (hata metnindeki dosya/port adları HTML olarak yorumlanmaz)."""
        box = QMessageBox(self)
        box.setIconPixmap(status_icon(kind))
        box.setWindowTitle(title)
        box.setTextFormat(Qt.PlainText)
        box.setText(str(text))
        box.exec()

    def _ask(self, title: str, text: str, yes: str, alt: str | None = None):
        """True = yes, "alt" = alternatif buton, None = iptal."""
        box = QMessageBox(self)
        box.setIconPixmap(status_icon("question"))
        box.setWindowTitle(title)
        box.setTextFormat(Qt.PlainText)
        box.setText(text)
        b_yes = box.addButton(yes, QMessageBox.AcceptRole)
        b_alt = box.addButton(alt, QMessageBox.DestructiveRole) if alt else None
        b_no = box.addButton(self.t("cancel"), QMessageBox.RejectRole)
        box.setDefaultButton(b_yes)
        box.setEscapeButton(b_no)
        box.exec()
        c = box.clickedButton()
        return True if c is b_yes else ("alt" if b_alt is not None and c is b_alt else None)

    # ================================================================ KAYDEDİLMEMİŞ VERİ / KAPANIŞ
    def _at_risk(self) -> list[Session]:
        """Kaydedilmeden kaybolacak oturumlar.

        Bir oturumda dosyaya yazılmamış satır ya da not varsa (Session.unsaved) risktedir.
        Bağlıyken ve otomatik kaydetme açıkken o anki kayıt, bağlantı kesilince zaten kaydedilir.
        """
        eng = self.engine
        cands = list(self._unsaved)
        if eng is not None and not self.chk_auto.isChecked():
            cands.append(eng.current)
        cands.append(self.view)
        out, seen = [], set()
        for s in cands:
            if (s is None or id(s) in seen or s.exporting or not s.unsaved()
                    or (eng is not None and s is eng.current and self.chk_auto.isChecked())):
                continue
            seen.add(id(s))
            out.append(s)
        self._unsaved = [s for s in self._unsaved if s.unsaved() or s.exporting]
        return out

    def _confirm_discard(self) -> bool:
        """Kaydedilmemiş veri varsa sorar. False = kullanıcı vazgeçti."""
        risk = self._at_risk()
        if not risk:
            return True
        t = self.t
        ans = self._ask(t("unsaved_title"), t("unsaved_ask", k=len(risk), n=_fmt_int(sum(s.n for s in risk))),
                        t("unsaved_save"), t("unsaved_discard"))
        if ans is None:
            return False
        if ans is not True:
            self._unsaved.clear()
            for s in risk:
                s.mark_saved()           # kullanıcı vazgeçti: tekrar sorulmasın
            return True
        if self.engine is not None and not self.chk_auto.isChecked():
            self._disconnect()           # o anki kayıt tamamlansın (listeye girer), sonra hepsi kaydedilir
        self._wait_exports()
        fmt = self.seg_fmt.value()
        for s in self._at_risk():        # diyalog açıkken durum değişmiş olabilir: yeniden hesapla
            self._save_session(s, mode="user", fmt=fmt)
        self._wait_exports()
        return not self._at_risk()

    def _wait_exports(self) -> None:
        """Bütün kayıt işleri (ve bitince sıraya giren yeniden kayıtlar) bitene kadar bekler."""
        dlg = None
        while True:
            self._drain_exports()          # biten işleri işle; bekleyen yeniden kayıt varsa yeni iş başlar
            if not any(x.is_alive() for x in self._export_threads):
                break
            if dlg is None:
                dlg = QProgressDialog(self.t("closing_wait"), None, 0, 0, self)
                dlg.setWindowTitle(APP_NAME)
                dlg.setWindowModality(Qt.WindowModal)
                dlg.setCancelButton(None)
                dlg.setMinimumDuration(0)
                dlg.show()
            QApplication.processEvents(QEventLoop.ExcludeUserInputEvents, 50)
            time.sleep(0.02)
        if dlg is not None:
            dlg.close()

    # ================================================================ YARDIM / HAKKINDA
    def show_help(self, topic: str | None = None) -> None:
        from .help_dialog import HelpDialog

        if self._help_dlg is None:
            self._help_dlg = HelpDialog(self, self.lang, str(ASSETS / "help"))
        if topic:
            self._help_dlg.show_topic(topic)
        self._help_dlg.show()
        self._help_dlg.raise_()
        self._help_dlg.activateWindow()

    def _first_run(self) -> None:
        s = self.settings
        st = system.unblock_status() if str(s.value("unblock_asked", "false")) != "true" else None
        if st is not None:
            s.setValue("unblock_asked", "true")
            # Teşhis: neden sorulduğu / sorulmadığı terminalde görünsün
            self._log(self.t("unblock_diag", exe=self.t("yes") if st["exe"] else self.t("no"), n=st["files"]), "muted")
            if st["exe"] or st["files"]:
                self._ask_unblock()
            else:
                self._msg("info", self.t("unblock_title"), self.t("unblock_clear"))
        if str(s.value("help_seen", "false")) != "true":
            s.setValue("help_seen", "true")
            self.show_help("quickstart")

    def _ask_unblock(self) -> None:
        t = self.t
        box = QMessageBox(self)
        box.setIconPixmap(status_icon("question"))
        box.setWindowTitle(t("unblock_title"))
        box.setText(t("unblock_ask"))
        yes = box.addButton(t("unblock_yes"), QMessageBox.AcceptRole)
        box.addButton(t("later"), QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is yes:
            self._do_unblock()

    def _do_unblock(self) -> None:
        t = self.t

        def done(res):
            ok, fail, skipped = res
            parts = [t("unblock_done", n=ok) if ok else t("unblock_none")]
            if fail:
                parts.append(t("unblock_fail", n=fail))
            if skipped:
                parts.append(t("unblock_skipped", n=skipped))
            text = "\n\n".join(parts)
            self._log(text.replace("\n\n", "  "), "warn" if (fail or skipped) else "ok")
            self._msg("warn" if (fail or skipped) else "info", t("unblock_title"), text)

        QApplication.setOverrideCursor(Qt.WaitCursor)

        def fin(fn):
            def inner(arg):
                QApplication.restoreOverrideCursor()
                fn(arg)
            return inner

        self._run_bg(lambda: system.unblock(system.app_dir()), fin(done),
                     fin(lambda e: self._msg("error", t("unblock_title"), str(e))))

    def _verify_files(self) -> None:
        t = self.t
        self.statusBar().showMessage(t("verify_running"), 5000)
        QApplication.setOverrideCursor(Qt.WaitCursor)

        def done(r):
            QApplication.restoreOverrideCursor()
            if r["no_manifest"]:
                self._msg("info", t("verify_btn"), t("verify_none"))
                return

            def lst(items, mark):
                return [f"  {mark} {x}" for x in items]

            if r["ok"] and not r["extra"]:
                self._log(t("verify_ok", n=r["checked"]).replace("\n\n", " "), "ok")
                self._msg("info", t("verify_btn"), t("verify_ok", n=r["checked"]))
            elif r["ok"]:
                items = "\n".join(lst(r["extra"][:15], "?") + (["  …"] if len(r["extra"]) > 15 else []))
                self._log(t("verify_btn") + ": " + ", ".join(r["extra"][:15]), "warn")
                self._msg("warn", t("verify_btn"), t("verify_ok_extra", n=r["checked"], m=len(r["extra"]), list=items))
            else:
                rows = lst(r["changed"], "≠") + lst(r["missing"], "−") + lst(r["extra"], "?")
                items = "\n".join(rows[:20] + (["  …"] if len(rows) > 20 else []))
                self._log(t("verify_btn") + ": " + "; ".join(rows[:20]), "error")
                self._msg("error", t("verify_btn"), t("verify_bad", c=len(r["changed"]), mi=len(r["missing"]),
                                                        e=len(r["extra"]), list=items))

        def err(e):
            QApplication.restoreOverrideCursor()
            self._msg("error", t("verify_btn"), str(e))

        self._run_bg(system.verify_install, done, err)

    def _run_bg(self, fn, on_ok, on_err) -> None:
        def job():
            try:
                res = fn()
                self._bg_q.put((on_ok, (res,)))
            except Exception as e:  # noqa: BLE001
                self._bg_q.put((on_err, (e,)))

        threading.Thread(target=job, name="bg", daemon=True).start()

    def _check_updates(self) -> None:
        t = self.t
        self.statusBar().showMessage(t("upd_checking"), 5000)
        self._log(t("upd_checking"), "info")

        def ok(res):
            if res["newer"]:
                box = QMessageBox(self)
                box.setIconPixmap(status_icon("info"))
                box.setWindowTitle(t("menu_updates"))
                box.setText(t("upd_new", new=res["latest"], cur=__version__))
                go = box.addButton(t("upd_open"), QMessageBox.AcceptRole)
                box.addButton(t("later"), QMessageBox.RejectRole)
                box.exec()
                if box.clickedButton() is go:
                    QDesktopServices.openUrl(QUrl(res["url"]))
            else:
                self._msg("info", t("menu_updates"), t("upd_latest", cur=__version__))

        def err(e):
            self._msg("warn", t("menu_updates"), t("upd_err", err=e))

        self._run_bg(system.check_latest, ok, err)

    def _report_issue(self) -> None:
        QDesktopServices.openUrl(QUrl(system.issue_url(self.lang)))

    def _show_about(self) -> None:
        from .help_dialog import AboutDialog

        can = sys.platform == "win32" and getattr(sys, "frozen", False)
        has_manifest = getattr(sys, "frozen", False) and os.path.exists(system.manifest_path())
        AboutDialog(self, self.lang, str(ASSETS / "icon.png"), can, self._do_unblock, self._check_updates,
                    on_verify=self._verify_files if has_manifest else None).exec()

    def closeEvent(self, ev) -> None:
        if not self._confirm_discard():
            ev.ignore()
            return
        if self.engine is not None:
            self._disconnect()
        self._wait_exports()
        if self._at_risk() and not self._confirm_discard():   # otomatik kayıt başarısız olduysa tekrar sor
            ev.ignore()
            return
        self._save_settings()
        super().closeEvent(ev)
