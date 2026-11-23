# SPDX-License-Identifier: GPL-3.0-or-later
"""Arayüz testleri (pencere gösterilmeden, QT_QPA_PLATFORM=offscreen).

Gerçek kullanıcı gibi: DEMO kartına bağlan, grafik/odak/ayrı pencere/filtre/not/alarm/kayıt, dil ve tema
değiştir, bağlantıyı kopar, dosya aç, pencereyi kapat. Hata olursa test düşer (Qt olayları içindeki
istisnalar da yakalanır).
"""
import os
import sys
import time

import numpy as np
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")

from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QInputDialog  # noqa: E402

from stm32logger.parser import parse_line  # noqa: E402
from stm32logger.session import Session  # noqa: E402

_errors: list = []


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([sys.argv[0], "--no-first-run"])
    old_hook = sys.excepthook
    sys.excepthook = lambda *e: _errors.append(e)          # Qt slotlarındaki istisnalar kaybolmasın
    yield a
    sys.excepthook = old_hook


@pytest.fixture
def win(app, tmp_path, monkeypatch):
    from stm32logger.ui import main_window as mw

    from stm32logger import system

    system.reset_settings(system.app_settings())          # her test temiz ayarlarla başlar
    msgs = []
    monkeypatch.setattr(mw.MainWindow, "_msg", lambda self, kind, title, text: msgs.append((kind, text)))
    monkeypatch.setattr(mw.MainWindow, "_ask", lambda self, *a, **k: "alt")    # "kaydetme" (diyalog açılmasın)
    monkeypatch.setattr(QDialog, "exec", lambda self: 0)                        # modal pencereler: iptal
    for name in ("getSaveFileName", "getOpenFileName"):                         # dosya pencereleri: iptal
        monkeypatch.setattr(QFileDialog, name, staticmethod(lambda *a, **k: ("", "")))
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: ""))
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("", False)))
    _errors.clear()
    w = mw.MainWindow()
    w.msgs = msgs
    w.folder_edit.setText(str(tmp_path / "kayıt klasörü"))              # Türkçe karakter ve boşluk
    w.resize(1366, 768)
    w.show()
    app.processEvents()
    yield w
    if w.engine is not None:
        w._disconnect()
    w._wait_exports()
    w._quiet_close = True
    w.close()
    w.deleteLater()
    app.processEvents()
    assert not _errors, _errors


def pump(app, w, sec):
    end = time.time() + sec
    while time.time() < end:
        app.processEvents()
        w._tick()
        time.sleep(0.01)


def connect_demo(app, w, mode="live"):
    from stm32logger.ui import main_window as mw

    w.port_cb.setCurrentIndex(w.port_cb.findData(mw.DEMO_PORT))
    w.seg_mode.set_value(mode)
    w._on_mode_changed(mode)
    w._connect()
    assert w.engine is not None
    pump(app, w, 1.0)


def fill(s: Session, n: int, ch: int = 4, gaps=()):
    """Oturuma hızlıca n satır ekler (sıra no'lu, isteğe bağlı kayıplı)."""
    with s.lock:
        seq = 0
        for i in range(n):
            if i in gaps:
                seq += 5
            vals = ",".join(f"{np.sin(i / 50 + k):.4f}" for k in range(ch))
            s.add(i * 0.001, parse_line(f"{seq},{vals}"))
            seq += 1
        s.flush_pending()


# ------------------------------------------------------------------ bağlantı ve canlı grafik
def test_demo_live_plot_and_stats(app, win):
    connect_demo(app, win)
    pump(app, win, 1.5)
    s = win.engine.current
    assert s.n > 30 and win._curves
    x, y = list(win._curves.values())[0].getData()
    assert len(x) == len(y) > 10
    win._disconnect()
    pump(app, win, 0.5)
    assert win.engine is None and s.ended_at is not None


def test_focus_fullscreen_detach_sidebar_bottom(app, win):
    connect_demo(app, win)
    h0 = win.plot.height()
    win._set_focus(True)
    app.processEvents()
    assert win.plot.height() >= h0
    win._set_focus(True, full=True)
    app.processEvents()
    win._set_focus(False)
    win._set_detached(True)
    pump(app, win, 0.5)
    win._apply_theme()                        # tema değişimi ayrı penceredeyken
    win._set_lang("en")
    win._set_lang("tr")
    win._set_detached(False)
    win._set_sidebar_collapsed(True)
    win._set_bottom_collapsed(True)
    pump(app, win, 0.3)
    win._set_sidebar_collapsed(False)
    win._set_bottom_collapsed(False)
    win._set_plot_collapsed(True)
    pump(app, win, 0.3)
    win._set_plot_collapsed(False)


def test_close_while_detached_and_connected(app, win):
    connect_demo(app, win)
    win._set_detached(True)
    win._set_focus(True, full=True)
    pump(app, win, 0.3)
    win.close()                               # kapanış sırasında hata olmamalı
    app.processEvents()


def test_channel_chips_and_filters(app, win):
    connect_demo(app, win)
    pump(app, win, 1.0)
    names = list(win._curves)
    win._toggle_channel(names[0], False)
    pump(app, win, 0.3)
    assert names[0] in win._hidden_ch
    win._toggle_channel(names[0], True)
    for kind in ("mavg", "median", "lowpass", "highpass", "notch", "off"):
        win.filt_cb.setCurrentIndex(win.filt_cb.findData(kind))
        win._plot_sig = None
        pump(app, win, 0.3)
    win.filt_cb.setCurrentIndex(win.filt_cb.findData("median"))
    from stm32logger import analysis
    assert win.filt_spin.maximum() == analysis.MEDIAN_MAX


def test_filter_on_gappy_session_no_crash(app, win):
    s = Session(mode="live", seq_mode="on", sample_rate=1000)
    fill(s, 3000, gaps={100, 103, 107, 1500})       # birbirine çok yakın kayıplar (kısa parçalar)
    s.finish("stop")
    win.view = s
    for kind in ("mavg", "median", "lowpass", "highpass", "notch"):
        win.filt_cb.setCurrentIndex(win.filt_cb.findData(kind))
        win._plot_sig = None
        win._update_plot()
        app.processEvents()
        x, y = list(win._curves.values())[0].getData()
        assert len(x) == len(y)
    assert len(win._gap_items) == 4


def test_gap_markers_reused_and_newest_shown(app, win):
    s = Session(mode="live", seq_mode="on")
    fill(s, 5000, ch=1, gaps=set(range(10, 4900, 40)))          # 122 kayıp: en yeni 100'ü işaretlenir
    s.finish("stop")
    win.view = s
    win._update_plot()
    items = list(win._gap_items)
    assert len(items) == 100
    last_x = list(win._curves.values())[0].getData()[0][-1]
    assert max(it.getRegion()[1] for it in items) > 0.9 * last_x
    win._plot_sig = None
    win._update_plot()
    assert win._gap_items == items                             # aynı nesneler (yeniden yaratılmadı)


def test_segment_rollover_keeps_plot(app, win):
    connect_demo(app, win)
    win.engine.segment_rows = 60                               # ~1.2 sn'de bir bölünsün
    pump(app, win, 4.0)
    s = win.engine.current
    assert s.prev_segment is not None
    win._plot_sig = None
    win._plot_t = 0
    win._update_plot()
    x = list(win._curves.values())[0].getData()[0]
    assert len(x) > s.n                                        # önceki parçanın sonu da çizildi
    assert x[0] > 0.5 or len(x) > 100                          # eksen her bölmede sıfırdan başlamadı


# ------------------------------------------------------------------ not, alarm, imleç, FFT, terminal
def test_notes_cursors_fft_terminal(app, win, monkeypatch):
    connect_demo(app, win)
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("deneme notu, virgüllü", True)))
    win._add_note()
    pump(app, win, 0.5)
    assert win.engine.current.notes
    win.btn_fft.setChecked(True)
    win._toggle_cursors(True)
    pump(app, win, 0.6)
    win._toggle_cursors(False)
    win.btn_fft.setChecked(False)
    win.send_edit.setText("GONDER")
    win._send()
    win.chk_hex_send.setChecked(True)
    win.send_edit.setText("zz")                                # geçersiz onaltılık: hata yazar, çökmez
    win._send()
    win.chk_hex_send.setChecked(False)
    win.chk_hex.setChecked(True)
    pump(app, win, 0.3)
    win.chk_hex.setChecked(False)
    win._quick_send(0)
    win._edit_alarms()
    win._edit_units()
    win._show_about()


# ------------------------------------------------------------------ kayıt ve dosya açma
def test_save_and_reopen(app, win, tmp_path, monkeypatch):
    s = Session(mode="live", seq_mode="on")
    fill(s, 2000)
    s.add_note("not 1", 0.5, row=100)
    s.finish("stop")
    win.view = s
    for fmt in ("csv", "xlsx", "txt"):
        win.seg_fmt.set_value(fmt)
        s.autosave_path = None
        win._save_session(s, mode="user", fmt=fmt)
        win._wait_exports()
        assert s.saved_path and s.saved_path.endswith("." + fmt) and os.path.exists(s.saved_path)
        path = s.saved_path
        monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (path, "")))
        win._open_saved_file()
        t0 = time.time()
        while win.view is s and time.time() - t0 < 30:          # arka planda açılır
            app.processEvents()
            time.sleep(0.02)
        assert win.view is not s and win.view.n == 2000 and win.view.notes
        win.view = s
    png = str(tmp_path / "grafik.png")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (png, "")))
    win._update_plot()
    win._save_png()
    assert os.path.getsize(png) > 1000


def test_save_error_does_not_crash(app, win, tmp_path):
    s = Session(mode="live", seq_mode="off")
    fill(s, 50)
    s.finish("stop")
    bad = tmp_path / "dosya"
    bad.write_text("x")
    win.folder_edit.setText(str(bad / "alt"))                  # klasör yerine dosya: yazılamaz
    win._save_session(s, mode="periodic")
    win._wait_exports()
    assert s.save_failed and s in win._unsaved


# ------------------------------------------------------------------ dil, tema, ayarlar
def test_lang_theme_and_settings_roundtrip(app, win):
    from stm32logger.ui import main_window as mw

    win._set_lang("en")
    for mode in ("dark", "light", "system"):
        win._set_theme(mode)
    win.filt_cb.setCurrentIndex(win.filt_cb.findData("notch"))
    win._set_sidebar_collapsed(True)
    win._save_settings()
    win.settings.sync()
    w2 = mw.MainWindow()
    try:
        assert w2.lang == "en" and w2.filt_cb.currentData() == "notch"
    finally:
        w2.close()
        w2.deleteLater()


def test_corrupt_settings_do_not_crash(app, win):
    from stm32logger.ui import main_window as mw

    from stm32logger import system

    st = system.app_settings()
    for k, v in {"lang": "xx", "window": "abc", "autosave_every": "-5", "collapsed": "{bozuk",
                 "filt_kind": "yok", "filt_params": "[1,2", "geometry": b"\x00\x01", "units": "{{",
                 "alarms": "5", "qcmds": "null", "history": "{}", "xaxis": 7, "bottom_tab": "99"}.items():
        st.setValue(k, v)
    st.sync()
    w2 = mw.MainWindow()
    w2.show()
    app.processEvents()
    w2.close()
    w2.deleteLater()


def test_small_screen_fits(app, win):
    # Test ekranı 800 piksel genişliğinde (≈ 1366×768 ekranda %150 ölçekten de dar):
    # araç satırı simgelere, sol panel şeride iner ve pencere ekrana sığar.
    scr = win.screen().availableGeometry()
    assert win._side_collapsed and win._compact and not win.lbl_x.isVisible()
    win.centralWidget().layout().activate()
    assert win.centralWidget().minimumSizeHint().width() <= scr.width()
    assert win.minimumWidth() <= scr.width() and win.minimumHeight() <= scr.height()
    win._set_lang("en")
    assert win.btn_png.text() == "🖼" and win.btn_png.toolTip() == "Save PNG"
    win._apply_compact(False)
    assert win.btn_png.text() == "Save PNG" and win.lbl_x.isVisible()

def test_net_port_items(app, win):
    win.port_cb.setCurrentIndex(max(0, win.port_cb.count() - 1))
    app.processEvents()
    win._refresh_ports()
    app.processEvents()


# ------------------------------------------------------------------ yardımcı pencereler
def test_dialogs_render(app, win):
    from stm32logger.ui.help_dialog import HelpDialog
    from stm32logger.ui.net_diag import NetDiagDialog
    from stm32logger.ui.splash import Splash
    from stm32logger.ui.tools import AlarmsDialog, QuickCmdDialog, UnitsDialog

    d = NetDiagDialog(win, win.t, win.disc)
    d.show()
    pump(app, win, 0.5)
    d._rescan()
    pump(app, win, 1.0)
    assert d.lbl_ips.text()
    d.close()
    u = UnitsDialog(win, "tr", ["Kanal 1", "Kanal 2"], {"Kanal 1": {"unit": "V", "scale": 2.0}})
    u.show()
    app.processEvents()
    assert isinstance(u.result_units(), dict)
    a = AlarmsDialog(win, "en", ["Kanal 1"], [{"ch": "Kanal 1", "op": ">", "value": 5.0}], {})
    a.show()
    app.processEvents()
    assert isinstance(a.result_alarms(), list)
    q = QuickCmdDialog(win, "tr", {"label": "X", "cmd": "GONDER", "hex": False, "eol": "\r\n", "repeat": 0.5})
    q.show()
    app.processEvents()
    assert q.result_cmd()["cmd"] == "GONDER"
    help_dir = os.path.join(os.path.dirname(__file__), "..", "help")
    if os.path.isdir(help_dir):
        h = HelpDialog(win, "tr", help_dir)
        h.show()
        app.processEvents()
        h.close()
    sp = Splash("tr", "4.0.0")
    sp.show()
    for i in range(3):
        sp.stage(i)
        app.processEvents()
    sp.close()
    for dlg in (u, a, q):
        dlg.close()


def test_update_dialog_runs_job(app, win, monkeypatch):
    from stm32logger.ui.update_dialog import UpdateDialog

    def fake_exec(self):
        self.show()
        t0 = time.time()
        while self.isVisible() and time.time() - t0 < 10:
            app.processEvents()
            time.sleep(0.01)
        return 1

    monkeypatch.setattr(QDialog, "exec", fake_exec)

    def job(progress, cancel):
        for i in range(5):
            progress(i, 5, "dl")
            time.sleep(0.05)
        return "tamam"

    assert UpdateDialog(win, win.t, "x").run(job) == ("ok", "tamam")

    def bad(progress, cancel):
        raise OSError("bağlantı yok")

    kind, err = UpdateDialog(win, win.t, "x").run(bad)
    assert kind == "err" and "bağlantı" in str(err)


def test_light_mode(app, win):
    connect_demo(app, win)
    win.act_light.setChecked(True)
    pump(app, win, 1.2)
    assert win._light and win._curves
    win._save_settings()
    win.act_light.setChecked(False)
    pump(app, win, 0.5)
    assert win.settings.value("light_mode") in (False, "false")
