"""Derlenen pakette hiç kullanılmayan Qt parçalarını siler (dosya parmak izleri bundan SONRA alınır).

    python packaging/prune_qt.py dist/Oskilo

PyInstaller, Qt eklentilerinin bağımlılıklarını da pakete koyar: sanal klavye eklentisi Qt Quick ve
QML'i, PDF resim eklentisi Qt PDF'i, ağ eklentileri Qt Network'ü getirir. Oskilo bunların hiçbirini
kullanmaz (arayüz Qt Widgets, grafik pyqtgraph, güncelleme Python'un kendi urllib'i). Ayrıca 20 MB'lık
yazılımsal OpenGL (opengl32sw) ve Qt'nin çeviri dosyaları gereksizdir. Daha az ve daha küçük dosya:
daha küçük indirme, daha hızlı ilk açılış (Windows Defender her dosyayı tarar).

Silinen bir parça gerçekten gerekliyse uygulama açılmaz: CI'daki --selftest ve güncelleme testi
bunu derleme sırasında yakalar.
macOS'ta çalıştırılmaz (paket imzası bozulmasın diye).
"""
import fnmatch
import shutil
import sys
from pathlib import Path

# PySide6 klasörüne göre yollar (glob). Windows ve Linux adları birlikte.
PATTERNS = [
    # Qt Quick / QML / sanal klavye (sadece sanal klavye eklentisi için gelir)
    "plugins/platforminputcontexts/*virtualkeyboard*",
    "Qt6Quick*.dll", "Qt6Qml*.dll", "Qt6VirtualKeyboard*.dll",
    "Qt/lib/libQt6Quick*.so*", "Qt/lib/libQt6Qml*.so*", "Qt/lib/libQt6VirtualKeyboard*.so*",
    "Qt/plugins/platforminputcontexts/*virtualkeyboard*",
    # Qt PDF (sadece PDF resim eklentisi için gelir)
    "plugins/imageformats/qpdf*", "Qt/plugins/imageformats/libqpdf*", "Qt6Pdf.dll", "Qt/lib/libQt6Pdf.so*",
    # Qt Network ve ağ eklentileri
    "plugins/tls", "plugins/networkinformation", "Qt/plugins/tls", "Qt/plugins/networkinformation",
    "Qt6Network.dll", "Qt/lib/libQt6Network.so*",
    "plugins/generic/qtuiotouchplugin*", "Qt/plugins/generic/libqtuiotouchplugin*",   # Qt Network ister
    # yazılımsal OpenGL yedeği (ekran kartı sürücüsü olmayan makineler için; Qt Widgets'a gerekmez).
    # Qt OpenGL modülleri KALIR: pyqtgraph eğri çizerken onları içe aktarıyor.
    "opengl32sw.dll", "plugins/platforms/qdirect2d.dll",
    # Qt Test, Qt'nin çevirileri (Oskilo kendi TR/EN metinlerini kullanır)
    "Qt6Test.dll", "Qt/lib/libQt6Test.so*", "translations", "Qt/translations",
]
KEEP_ALWAYS = ("Qt6Core", "Qt6Gui", "Qt6Widgets", "Qt6Svg", "qwindows", "qoffscreen", "qsvg", "qxcb")


def prune(dist: Path, dry: bool = False) -> tuple[int, int]:
    pyside = dist / "_internal" / "PySide6"
    if not pyside.is_dir():
        raise SystemExit(f"PySide6 klasörü yok: {pyside}")
    root = pyside.resolve()
    targets = set()
    for pat in PATTERNS:
        for p in pyside.glob(pat):
            rp = p.resolve()
            if root not in rp.parents:
                continue
            if any(k in p.name for k in KEEP_ALWAYS):
                raise SystemExit(f"temel bir Qt parçası silinmek üzereydi, durduruldu: {p}")
            targets.add(p)
    files = n_bytes = 0
    for p in sorted(targets):
        if p.is_dir():
            for f in p.rglob("*"):
                if f.is_file():
                    files += 1
                    n_bytes += f.stat().st_size
            if not dry:
                shutil.rmtree(p)
        elif p.is_file():
            files += 1
            n_bytes += p.stat().st_size
            if not dry:
                p.unlink()
        print(("(deneme) " if dry else "") + "silindi: " + p.relative_to(dist).as_posix())
    return files, n_bytes


if __name__ == "__main__":
    if sys.platform == "darwin":
        print("macOS: atlandı")
        sys.exit(0)
    d = Path(sys.argv[1])
    files, n_bytes = prune(d, dry="--dry-run" in sys.argv)
    if "--dry-run" not in sys.argv:                 # Linux: silinen kütüphanelere giden kısayollar
        for p in (d / "_internal").rglob("*"):
            if p.is_symlink() and not p.exists():
                p.unlink()
                print("silindi (kısayol): " + p.relative_to(d).as_posix())
    print(f"{files} dosya, {n_bytes / 1e6:.1f} MB kaldırıldı")
    # basit güvenlik: geriye Qt'nin temel parçaları kalmış olmalı
    left = [p.name for p in (d / "_internal" / "PySide6").rglob("*") if p.is_file()]
    for need in ("QtCore", "QtGui", "QtWidgets"):
        if not any(fnmatch.fnmatch(n, f"*{need}*") for n in left):
            raise SystemExit(f"{need} eksik!")
    sys.exit(0)
