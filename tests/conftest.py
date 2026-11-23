# SPDX-License-Identifier: GPL-3.0-or-later
"""Testler gerçek kullanıcı ayarlarına (Windows kayıt defteri / ~/.config) dokunmaz; pencere gösterilmez."""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_TMP = tempfile.mkdtemp(prefix="oskilo_test_")
os.environ["OSKILO_TEST_HOME"] = _TMP

try:
    from PySide6.QtCore import QSettings

    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, os.path.join(_TMP, "settings"))
except ImportError:          # PySide6 yoksa (sadece çekirdek testleri) sorun değil
    pass
