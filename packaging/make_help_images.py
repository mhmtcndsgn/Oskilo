"""Kılavuz görsellerini (assets/help/<dil>/*.png) ve README ekran görüntülerini (docs/*.png)
uygulamanın kendisinden üretir.

Arayüz değiştiğinde çalıştırın:
    python packaging/make_help_images.py

Ekran olmadan da çalışır (QT_QPA_PLATFORM=offscreen). Kılavuz görselleri 2 kat çözünürlükte
üretilir; uygulama bunları ekranın ölçeğine göre pürüzsüz küçültür (pikselleşmez).
Numaralı sarı rozetler kılavuz metnindeki numaralarla aynı sıradadır (bkz. stm32logger/help.py).
Not: Bu betik bilgisayardaki Oskilo ayarlarını sıfırlar.
"""
import math
import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
SCALE = 2
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.argv.append("--no-first-run")

from PySide6.QtCore import QPoint, QRect, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from stm32logger.system import app_settings, reset_settings  # noqa: E402
from stm32logger.engine import DEMO_PORT  # noqa: E402
from stm32logger.parser import parse_line  # noqa: E402
from stm32logger.session import Session  # noqa: E402

ACCENT = QColor("#e3b341")
OUT = os.path.join(ROOT, "assets", "help")
DOCS = os.path.join(ROOT, "docs")
WIN_FOLDER = r"C:\Users\user\Documents\Oskilo"


def pump(app, sec):
    t = time.time()
    while time.time() - t < sec:
        app.processEvents()
        time.sleep(0.01)


def wrect(win, widget) -> QRect:
    return QRect(widget.mapTo(win, QPoint(0, 0)), widget.size())


def union(*rects) -> QRect:
    r = QRect(rects[0])
    for x in rects[1:]:
        r = r.united(x)
    return r


def scene_rect(win, plot, item) -> QRect:
    poly = plot.mapFromScene(item.sceneBoundingRect())
    r = poly.boundingRect().intersected(plot.viewport().rect())
    return QRect(plot.viewport().mapTo(win, r.topLeft()), r.size())


def grab(w):
    """Pencerenin SCALE kat çözünürlüklü görüntüsü."""
    pix = w.grab()
    return pix


def annotate(pix, marks, crop: QRect, path):
    """marks: [(rect_pencerede, numara)]  — çizim mantıksal koordinatla, kırpma gerçek pikselle."""
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    f = QFont()
    f.setBold(True)
    f.setPixelSize(13)
    p.setFont(f)
    for rect, num in marks:
        r = QRectF(rect).adjusted(-3, -3, 3, 3)
        p.setPen(QPen(ACCENT, 2))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r, 7, 7)
        d = 24
        cx = max(crop.left() + d / 2 + 1, r.left())
        cy = max(crop.top() + d / 2 + 1, r.top())
        badge = QRectF(cx - d / 2, cy - d / 2, d, d)
        p.setPen(QPen(QColor("#0d1117"), 2))
        p.setBrush(QBrush(ACCENT))
        p.drawEllipse(badge)
        p.setPen(QColor("#0d1117"))
        p.drawText(badge, Qt.AlignCenter, str(num))
    p.end()
    dpr = pix.devicePixelRatio()
    phys = QRect(int(crop.x() * dpr), int(crop.y() * dpr), int(crop.width() * dpr), int(crop.height() * dpr))
    img = pix.copy(phys).toImage()
    img.setDevicePixelRatio(1.0)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path, "PNG")
    print("  ", os.path.relpath(path, ROOT), img.width(), "x", img.height())


def save_plain(w, path):
    """README için normal (1x) ekran görüntüsü."""
    img = w.grab().toImage().scaledToWidth(w.width(), Qt.SmoothTransformation)
    img.setDevicePixelRatio(1.0)
    img.save(path, "PNG")
    print("  ", os.path.relpath(path, ROOT), img.width(), "x", img.height())


def tidy(w):
    """Görsellerde Linux geçici yolları görünmesin: Windows'taki gibi göster."""
    name = os.path.basename(w.last_saved or "deney.xlsx")
    w.folder_edit.setText(WIN_FOLDER)
    w.term.clear()
    w.term.appendPlainText("3997,2031,2129\n3998,2043,2123\n3999,2036,2119\nEND,4000")
    w._log(w.t("msg_saved", path=WIN_FOLDER + "\\" + name), "ok")


def numbered(rects):
    return [(r, i) for i, r in enumerate(rects, start=1)]


def lossy_session(w) -> Session:
    s = Session(mode="dma", sample_rate=1000, port="COM5", baud=921600, channel_prefix=w.t("channel_prefix"))
    s.add(0, parse_line("START,3000"))
    for i in range(3000):
        if 1450 < i < 1780:
            continue
        a = 2048 + 1200 * math.sin(i / 90) * math.exp(-i / 1500)
        b = 2048 + 500 * math.cos(i / 160) * math.exp(-i / 2500)
        s.add(0, parse_line(f"{i},{a:.0f},{b:.0f}"))
    s.add(0, parse_line("END,3000"))
    s.finish("end")
    return s


def make(lang: str, app) -> None:
    from stm32logger.ui.main_window import MainWindow

    st = app_settings()
    st.clear()
    st.setValue("theme_mode", "dark")
    st.setValue("accent", "oskilo")
    w = MainWindow()
    w._ask = lambda *a: "alt"          # kapanışta "kaydedilmemiş veri" sorusu çıkmasın
    w.lang_cb.setCurrentIndex(0 if lang == "tr" else 1)
    w.folder_edit.setText(tempfile.mkdtemp(prefix="help_"))
    w.prefix_edit.setText("deney" if lang == "tr" else "experiment")
    w.fs_spin.setValue(1000)
    w.baud_cb.setCurrentText("921600")
    w.seg_mode.set_value("dma")
    w._on_mode_changed("dma")
    w.port_cb.setCurrentIndex(w.port_cb.findData(DEMO_PORT))
    w.resize(1360, 860)
    w.show()
    pump(app, 0.5)
    d = os.path.join(OUT, lang)

    # --- modes: aktarım sürerken üst kutucuklar + ilerleme çubuğu
    w.baud_cb.setCurrentText("115200")
    w._connect()
    t0 = time.time()
    while time.time() - t0 < 10 and not (w.progress.isVisible() and w.progress.value() > 350):
        pump(app, 0.05)
    pump(app, 0.1)
    pix = grab(w)
    crop = union(wrect(w, w.tile_rows), wrect(w, w.tile_status), wrect(w, w.progress)).adjusted(-14, -14, 14, 14)
    annotate(pix, numbered([wrect(w, w.tile_rows), wrect(w, w.progress), wrect(w, w.tile_status)]),
             crop, os.path.join(d, "modes.png"))

    # --- quickstart: aktarım bitti, bant görünüyor
    t0 = time.time()
    while time.time() - t0 < 20 and not w.last_saved:
        pump(app, 0.05)
    w._disconnect()
    pump(app, 0.6)
    tidy(w)
    pump(app, 0.2)
    pix = grab(w)
    crop = wrect(w, w.card_conn).adjusted(-10, -10, 10, 10)
    annotate(pix, numbered([wrect(w, w.port_cb), wrect(w, w.seg_mode), wrect(w, w.btn_connect)]),
             crop, os.path.join(d, "quickstart_a.png"))
    pix = grab(w)
    crop = union(wrect(w, w.tile_rows), wrect(w, w.tile_status), wrect(w, w.banner)).adjusted(-14, -14, 14, 14)
    annotate(pix, [(union(wrect(w, w.tile_rows), wrect(w, w.tile_status)), 4), (wrect(w, w.banner), 5)],
             crop, os.path.join(d, "quickstart_b.png"))
    if lang == "en":
        save_plain(w, os.path.join(DOCS, "screenshot.png"))
    else:
        save_plain(w, os.path.join(DOCS, "ekran_deney.png"))

    # --- chart: grafik + terminal
    w.term.appendPlainText("SEND" if lang == "en" else "GONDER")
    pix = grab(w)
    plot_panel = w.plot_split.parentWidget()
    term_panel = w.bottom_panel
    crop = union(wrect(w, plot_panel), wrect(w, term_panel)).adjusted(-8, -8, 8, 8)
    qbtns = list(w._qc_buttons) + [w._qc_add]
    marks = [wrect(w, w.x_cb), wrect(w, w.win_cb), wrect(w, w.btn_fit), wrect(w, w.btn_png),
             scene_rect(w, w.plot, w.legend), union(wrect(w, w.chk_hex), wrect(w, w.btn_clear)),
             union(*[wrect(w, b) for b in qbtns]), union(wrect(w, w.send_edit), wrect(w, w.btn_send)),
             union(wrect(w, w.btn_plot_toggle), wrect(w, w.plot_hdr.title))]
    annotate(pix, numbered(marks), crop, os.path.join(d, "chart.png"))

    # --- analysis: imleç + FFT + not + istatistik
    s = w.view
    s.add_note("motor" if lang == "en" else "motor açıldı", s.t_host[1500])
    s.notes[-1] = (1500, s.notes[-1][1], s.notes[-1][2])
    w._plot_sig = None
    w._notes_sig = None
    w.resize(1360, 1000)
    w.btn_fft.setChecked(True)
    w.tabs.setCurrentIndex(1)
    pump(app, 0.3)
    w._update_plot()
    pump(app, 0.4)
    w.btn_cursor.setChecked(True)
    w._cursor_lines[0].setValue(0.62)
    w._cursor_lines[1].setValue(0.62 + 1 / 2.5)
    w._update_analysis()
    pump(app, 0.6)
    pix = grab(w)
    crop = union(wrect(w, plot_panel), wrect(w, term_panel)).adjusted(-8, -8, 8, 8)
    marks = [wrect(w, w.btn_cursor), wrect(w, w.lbl_readout),
             wrect(w, w.btn_fft), wrect(w, w.fft_plot), wrect(w, w.btn_note),
             scene_rect(w, w.plot, next(iter(w._note_items.values())).label), wrect(w, w.stats_table)]
    annotate(pix, numbered(marks), crop, os.path.join(d, "analysis.png"))
    w.btn_cursor.setChecked(False)
    w.btn_fft.setChecked(False)
    w.tabs.setCurrentIndex(0)
    s.notes.clear()

    # --- alarms: eşik çizgisi + uyarı bandı + otomatik not, ve alarm penceresi
    from stm32logger.ui.tools import AlarmsDialog

    ch1, ch2 = s.channels[0], s.channels[1]
    w._alarms = [{"on": True, "ch": ch1, "op": ">", "value": 3200.0, "hyst": 200.0, "beep": True, "cmd": "STOP"},
                 {"on": True, "ch": ch2, "op": "<", "value": 1600.0, "hyst": 50.0, "beep": False, "cmd": ""}]
    row = next(i for i, v in enumerate(s.cols[1]) if v < 1600)
    w._fire_alarm(1, w._alarms[1], s, row, float(s.cols[1][row]), s.t_host[row], 1)
    w._alarm_sig = None
    w._plot_sig = None
    w._update_plot()
    pump(app, 0.4)
    pix = grab(w)
    crop = union(wrect(w, w.banner), wrect(w, plot_panel)).adjusted(-8, -8, 8, 8)
    note_lbl = next(iter(w._note_items.values())).label
    marks = [wrect(w, w.banner), scene_rect(w, w.plot, w._alarm_lines[1].label), scene_rect(w, w.plot, note_lbl)]
    annotate(pix, numbered(marks), crop, os.path.join(d, "alarms.png"))
    dlg = AlarmsDialog(w, lang, [ch1, ch2], w._alarms, {})
    dlg.resize(900, 300)
    dlg.show()
    pump(app, 0.4)
    dp = dlg.grab()
    annotate(dp, [], QRect(0, 0, dlg.width(), dlg.height()), os.path.join(d, "alarms_dlg.png"))
    dlg.close()
    w._alarms = []
    w._alarm_sig = None
    w.banner.hide()
    s.notes.clear()
    w._plot_sig = None
    w.resize(1360, 860)
    pump(app, 0.3)

    # --- connect + saving: yan panel tamamı görünsün diye pencereyi uzat
    w.resize(1360, 1500)
    pump(app, 0.5)
    pix = grab(w)
    crop = union(wrect(w, w.card_conn), wrect(w, w.card_data)).adjusted(-10, -10, 10, 10)
    marks = [wrect(w, w.port_cb), wrect(w, w.btn_refresh), wrect(w, w.baud_cb), wrect(w, w.seg_mode),
             wrect(w, w.seg_dfmt), wrect(w, w.chk_reconnect), wrect(w, w.btn_connect), wrect(w, w.fs_spin),
             wrect(w, w.seq_cb), wrect(w, w.idle_spin), wrect(w, w.btn_units), wrect(w, w.btn_alarms)]
    annotate(pix, numbered(marks), crop, os.path.join(d, "connect.png"))

    pix = grab(w)
    crop = wrect(w, w.card_save).adjusted(-10, -10, 10, 10)
    marks = [wrect(w, w.seg_fmt), union(wrect(w, w.folder_edit), wrect(w, w.btn_browse)), wrect(w, w.prefix_edit),
             wrect(w, w.chk_auto), union(wrect(w, w.lbl_every), wrect(w, w.every_cb)), wrect(w, w.chk_raw),
             wrect(w, w.btn_open_folder), wrect(w, w.btn_open_saved),
             wrect(w, w.btn_save_quick), wrect(w, w.btn_save_as)]
    annotate(pix, numbered(marks), crop, os.path.join(d, "saving.png"))

    # --- trouble: kayıplı bir aktarım
    w.resize(1360, 860)
    w.chk_auto.setChecked(False)
    pump(app, 0.3)
    w._on_session_done(lossy_session(w))
    pump(app, 1.0)
    pix = grab(w)
    crop = union(wrect(w, w.tile_rows), wrect(w, w.plot)).adjusted(-12, -12, 12, 12)
    marks = [wrect(w, w.banner), wrect(w, w.tile_missing), scene_rect(w, w.plot, w._gap_items[0])]
    annotate(pix, numbered(marks), crop, os.path.join(d, "trouble.png"))
    save_plain(w, os.path.join(DOCS, "screenshot_gap.png" if lang == "en" else "ekran_kayip.png"))

    # --- README: kılavuz penceresi
    w.show_help("analysis")
    dlg = w._help_dlg
    dlg.resize(1100, 760)
    pump(app, 0.8)
    img = dlg.grab().toImage().scaledToWidth(dlg.width(), Qt.SmoothTransformation)
    img.setDevicePixelRatio(1.0)
    img.save(os.path.join(DOCS, f"guide_{lang}.png"), "PNG")
    dlg.close()

    w.close()
    w.deleteLater()
    pump(app, 0.2)


def main():
    os.environ["QT_SCALE_FACTOR"] = str(SCALE)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    f = QFont()
    f.setFamilies(["Segoe UI", "Inter", "DejaVu Sans"])
    f.setPointSizeF(9.5)
    app.setFont(f)
    for old in ("quickstart.png",):
        for lang in ("tr", "en"):
            p = os.path.join(OUT, lang, old)
            if os.path.exists(p):
                os.remove(p)
    for lang in ("tr", "en"):
        print(lang)
        make(lang, app)
    reset_settings(app_settings())


if __name__ == "__main__":
    main()
