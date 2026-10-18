"""Oskilo — giriş noktası.

Kaynak koddan çalıştırma:
    pip install -r requirements.txt
    python main.py

Komut satırı seçenekleri:
    --reset-settings     tüm ayarları sıfırlayıp açar (ayar bozulduysa)
    --no-first-run       ilk açılış pencerelerini göstermez
    --selftest out.txt   uçtan uca otomatik test (CI)
"""
import datetime
import faulthandler
import os
import sys
import time
import traceback
from pathlib import Path

LOG_MAX = 512 * 1024
_hook_busy = False
_last_dialog = 0.0
_fault_file = None


def _log_dir() -> Path:
    """Hata kayıtlarının klasörü (kullanıcının belgelerini kirletmez)."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "Oskilo"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Logs" / "Oskilo"
    else:
        base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "oskilo"
    try:
        base.mkdir(parents=True, exist_ok=True)
        return base
    except OSError:
        return Path.home()


def _crash_log_path() -> str:
    return str(_log_dir() / "oskilo_hata.txt")


def _append_log(path: str, text: str) -> None:
    try:
        if os.path.exists(path) and os.path.getsize(path) > LOG_MAX:   # dosya sonsuza kadar büyümesin
            with open(path, "rb") as f:
                f.seek(-LOG_MAX // 2, os.SEEK_END)
                tail = f.read()
            with open(path, "wb") as f:
                f.write(tail)
        with open(path, "a", encoding="utf-8") as f:
            f.write(text)
    except OSError:
        pass


def _ui_lang() -> str:
    try:
        from stm32logger.system import app_settings

        lang = str(app_settings().value("lang", "tr"))
        return lang if lang in ("tr", "en") else "tr"
    except Exception:  # noqa: BLE001
        return "tr"


def _excepthook(exc_type, exc, tb):
    global _hook_busy, _last_dialog
    err = "".join(traceback.format_exception(exc_type, exc, tb))
    path = _crash_log_path()
    _append_log(path, f"\n===== {datetime.datetime.now():%Y-%m-%d %H:%M:%S} =====\n{err}")
    sys.__excepthook__(exc_type, exc, tb)
    # Hata penceresi açıkken yeni hata gelirse ya da hatalar art arda geliyorsa pencere yığılmasın
    if _hook_busy or time.monotonic() - _last_dialog < 5:
        return
    _hook_busy = True
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication, QMessageBox

        if QApplication.instance():
            from stm32logger.i18n import T

            box = QMessageBox(QMessageBox.Critical, "Oskilo", T(_ui_lang())("crash", path=path))
            box.setTextFormat(Qt.PlainText)
            box.setDetailedText(err[-4000:])
            box.exec()
    except Exception:  # noqa: BLE001
        pass
    finally:
        _last_dialog = time.monotonic()
        _hook_busy = False


def _single_instance(app):
    """Uygulama zaten açıksa sorar. Döndürür: kilit nesnesi ya da None (=çık)."""
    from PySide6.QtCore import QDir, QLockFile
    from PySide6.QtWidgets import QMessageBox

    from stm32logger.i18n import T

    lock = QLockFile(os.path.join(QDir.tempPath(), "oskilo_mhmtcndsgn.lock"))
    lock.setStaleLockTime(0)
    if lock.tryLock(100):
        return lock
    t = T(_ui_lang())
    box = QMessageBox(QMessageBox.Question, "Oskilo", t("already_running"))
    go = box.addButton(t("open_anyway"), QMessageBox.AcceptRole)
    box.addButton(t("cancel"), QMessageBox.RejectRole)
    box.exec()
    return lock if box.clickedButton() is go else None


def main() -> int:
    global _fault_file
    sys.excepthook = _excepthook
    try:   # Python seviyesinde yakalanamayan çökmeler (ör. sürücü hatası) de kayda geçsin
        _fault_file = open(_log_dir() / "oskilo_cokme.txt", "a", encoding="utf-8")
        faulthandler.enable(_fault_file)
    except (OSError, RuntimeError, ValueError):
        pass

    if sys.platform == "win32":
        # Görev çubuğunda Python yerine uygulamanın kendi ikonu görünsün
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("mhmtcndsgn.oskilo")
        except Exception:
            pass

    from PySide6.QtGui import QFont, QIcon
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    app.setApplicationName("Oskilo")
    app.setOrganizationName("mhmtcndsgn")
    app.setStyle("Fusion")

    if "--reset-settings" in sys.argv:
        from stm32logger.system import app_settings, reset_settings

        reset_settings(app_settings())
    lock = None
    if "--selftest" not in sys.argv:
        lock = _single_instance(app)
        if lock is None:
            return 0

    font = QFont()
    font.setFamilies(["Segoe UI", "Inter", "SF Pro Text", "Helvetica Neue", "Arial"])
    font.setPointSizeF(9.5)
    app.setFont(font)

    from stm32logger.ui import theme
    from stm32logger.ui.main_window import ASSETS, MainWindow

    app.setStyleSheet(theme.build_qss(ASSETS))
    icon = ASSETS / "icon.png"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))

    win = MainWindow()
    win.show()
    if "--selftest" in sys.argv:
        _selftest(app, win)
    rc = app.exec()
    if lock is not None:
        lock.unlock()
    return rc


def _selftest(app, win) -> None:
    """Paketlenmiş uygulamanın uçtan uca çalıştığını doğrular (CI'da kullanılır).

    Oskilo --selftest sonuc.txt
    DEMO portuna bağlanır, bir deney aktarımı alır, Excel + PNG kaydeder, çıkar.
    """
    import tempfile
    import time

    from PySide6.QtCore import QTimer

    from PySide6.QtGui import QPixmap

    from stm32logger.engine import DEMO_PORT
    from stm32logger.help import image_names
    from stm32logger.ui.main_window import ASSETS

    from stm32logger import system

    # Kılavuz görselleri pakete girmiş mi? Kılavuz penceresi açılıyor mu?
    missing_help = [f"{lang}/{n}" for lang in ("tr", "en") for n in sorted(image_names())
                    if QPixmap(str(ASSETS / "help" / lang / f"{n}.png")).isNull()]
    win.show_help("quickstart")
    win._help_dlg.close()
    # Windows paketinde parmak izi listesi var mı ve klasör onunla birebir aynı mı?
    integrity = "n/a"
    if getattr(sys, "frozen", False) and os.path.exists(system.manifest_path()):
        r = system.verify_install()
        integrity = "ok" if r["ok"] and not r["extra"] else f"BAD changed={r['changed'][:5]} " \
            f"missing={r['missing'][:5]} extra={r['extra'][:5]}"
    elif sys.platform == "win32" and getattr(sys, "frozen", False):
        integrity = "NO-MANIFEST"

    i = sys.argv.index("--selftest")
    out = sys.argv[i + 1] if len(sys.argv) > i + 1 else "selftest.txt"
    d = tempfile.mkdtemp(prefix="datalogger_")
    win.folder_edit.setText(d)
    win.seg_mode.set_value("dma")
    win._on_mode_changed("dma")
    win.seg_fmt.set_value("xlsx")
    win.chk_auto.setChecked(True)
    win.baud_cb.setCurrentText("921600")
    win.port_cb.setCurrentIndex(win.port_cb.findData(DEMO_PORT))
    win._connect()
    deadline = time.time() + 40

    def finish(ok: bool, msg: str) -> None:
        with open(out, "w", encoding="utf-8") as f:
            f.write(("OK " if ok else "FAIL ") + msg + "\n")
        win._disconnect()
        app.exit(0 if ok else 1)

    def poll() -> None:
        try:
            if win.last_saved and os.path.exists(win.last_saved):
                from pyqtgraph.exporters import ImageExporter

                png = os.path.join(d, "grafik.png")
                ImageExporter(win.plot.getPlotItem()).export(png)
                v = win.view
                ok = (v is not None and v.n == 4000 and v.missing == 0 and os.path.getsize(png) > 1000
                      and not missing_help and integrity in ("ok", "n/a"))
                finish(ok, f"rows={v.n if v else 0} missing={v.missing if v else '?'} "
                           f"help_missing={missing_help} integrity={integrity} xlsx={win.last_saved}")
                return
            if time.time() > deadline:
                finish(False, "timeout")
                return
        except Exception as e:  # noqa: BLE001
            finish(False, repr(e))
            return
        QTimer.singleShot(200, poll)

    QTimer.singleShot(200, poll)


if __name__ == "__main__":
    sys.exit(main())
