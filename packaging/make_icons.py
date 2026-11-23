# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Mehmet Can Dusgun (mhmtcndsgn) — Oskilo
"""Uygulama simgelerini assets/logo.svg'den üretir: icon.png, icon.ico (Windows), icon.icns (macOS).

    python packaging/make_icons.py

Küçük boyutlar (16, 24, 32 px) büyük görselden küçültülmez, SVG'den doğrudan çizilir; böylece
görev çubuğunda da keskin görünür.
"""
import io
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PIL import Image  # noqa: E402
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "assets")


def render(svg: bytes, size: int) -> Image.Image:
    r = QSvgRenderer(QByteArray(svg))
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(0)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    r.render(p, QRectF(0, 0, size, size))
    p.end()
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return Image.open(io.BytesIO(bytes(buf.data()))).convert("RGBA")


def main():
    QGuiApplication(sys.argv)
    svg = open(os.path.join(ASSETS, "logo.svg"), "rb").read()
    big = render(svg, 1024)
    big.save(os.path.join(ASSETS, "icon.png"))
    ico_sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    imgs = [render(svg, s) for s in ico_sizes]
    imgs[-1].save(os.path.join(ASSETS, "icon.ico"), format="ICO",
                  sizes=[(s, s) for s in ico_sizes], append_images=imgs[:-1])
    big.save(os.path.join(ASSETS, "icon.icns"), format="ICNS")
    print("icon.png, icon.ico, icon.icns ->", ASSETS)


if __name__ == "__main__":
    main()
