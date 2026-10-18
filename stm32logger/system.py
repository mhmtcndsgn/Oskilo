"""İşletim sistemiyle ilgili küçük yardımcılar.

* Kurulum bütünlüğü: derleme sırasında her dosyanın SHA-256 parmak izi
  `manifest.sha256` dosyasına yazılır. Uygulama klasöründe değişen, eksik ya da
  sonradan eklenmiş (tanınmayan) dosyaları bulur.
* Windows engel kaldırma: sadece parmak izi tutan, kendi dosyalarımızdan kaldırılır.
* Güncelleme kontrolü (sadece kullanıcı isteyince) ve hata bildirimi bağlantısı.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

from . import APP_NAME, OLD_SETTINGS_APPS, ORG_NAME, REPO_URL, SETTINGS_APP, __version__

ZONE = ":Zone.Identifier"   # Windows "internetten indirildi" etiketi (NTFS alternatif akış)
MANIFEST = "manifest.sha256"


# ---------------------------------------------------------------- ayarlar
_MIGRATED = "_migrated_from_old"


def app_settings():
    """Uygulamanın ayarları. İlk açılışta önceki adla (Data Logger) kaydedilmiş ayarlar taşınır,
    böylece güncelleyen kullanıcının kayıt klasörü, tema, birimler vb. kaybolmaz."""
    from PySide6.QtCore import QSettings

    s = QSettings(ORG_NAME, SETTINGS_APP)
    s.setFallbacksEnabled(False)          # kuruluş düzeyindeki ortak ayarlar bu uygulamanınmış gibi sayılmasın
    if not s.contains(_MIGRATED):
        for old_name in OLD_SETTINGS_APPS:
            old = QSettings(ORG_NAME, old_name)
            old.setFallbacksEnabled(False)
            keys = old.allKeys()
            if keys:
                for k in keys:
                    if not s.contains(k):
                        s.setValue(k, old.value(k))
                break
        s.setValue(_MIGRATED, "true")     # bir daha taşınmasın (sıfırlansa bile)
        s.sync()
    return s


def reset_settings(s) -> None:
    """Ayarları sıfırlar; eski sürüm ayarları tekrar taşınmaz."""
    s.clear()
    s.setValue(_MIGRATED, "true")
    s.sync()


# ---------------------------------------------------------------- Windows başlık çubuğu
def _colorref(hexcol: str) -> int:
    r, g, b = (int(hexcol[i:i + 2], 16) for i in (1, 3, 5))
    return r | (g << 8) | (b << 16)


def set_title_bar(hwnd: int, caption: str, text: str, border: str, dark: bool) -> bool:
    """Pencerenin başlık çubuğunu tema renklerine boyar (Windows).

    Windows 11: başlık, yazı ve kenar rengi doğrudan verilir.
    Windows 10: renk verilemez; sadece koyu/açık başlık seçilir.
    Döndürür: renk uygulandıysa True. Hata olursa sessizce False (pencere varsayılan kalır).
    """
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        dwm = ctypes.windll.dwmapi

        def setattr_(attr: int, value: int) -> bool:
            v = ctypes.c_uint(value)
            return dwm.DwmSetWindowAttribute(wintypes.HWND(hwnd), ctypes.c_uint(attr),
                                             ctypes.byref(v), ctypes.sizeof(v)) == 0

        if not setattr_(20, 1 if dark else 0):      # DWMWA_USE_IMMERSIVE_DARK_MODE
            setattr_(19, 1 if dark else 0)          # Windows 10 1809–1909'daki eski numara
        ok = setattr_(35, _colorref(caption))       # DWMWA_CAPTION_COLOR (Windows 11+)
        if ok:
            setattr_(36, _colorref(text))           # DWMWA_TEXT_COLOR
            setattr_(34, _colorref(border))         # DWMWA_BORDER_COLOR
        # açık pencerede başlık hemen yeniden çizilsin
        ctypes.windll.user32.SetWindowPos(wintypes.HWND(hwnd), None, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020)
        return ok
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------- Windows engel kaldırma
def app_dir() -> str:
    """Paketlenmiş uygulamada exe'nin klasörü, kaynak koddan çalışırken proje klasörü."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def manifest_path(root: str | None = None) -> str:
    """Paketlenmiş uygulamada _internal/manifest.sha256."""
    if root is None and getattr(sys, "_MEIPASS", None):
        return os.path.join(sys._MEIPASS, MANIFEST)
    return os.path.join(root or app_dir(), "_internal", MANIFEST)


def read_manifest(path: str) -> dict[str, str]:
    """{göreli_yol: sha256}. Yol ayırıcısı her zaman '/'."""
    out = {}
    with open(path, "r", encoding="utf-8") as f:
        for ln in f:
            ln = ln.rstrip("\r\n")
            if not ln or ln.startswith("#"):
                continue
            digest, _, rel = ln.partition("  ")
            if len(digest) == 64 and rel and ".." not in rel.split("/") and not os.path.isabs(rel):
                out[rel] = digest.lower()
    return out


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_manifest(root: str, out_path: str) -> int:
    """Derleme sırasında çağrılır (packaging/make_manifest.py)."""
    rows = []
    out_abs = os.path.abspath(out_path)
    for dirpath, dirs, files in os.walk(root):
        dirs.sort()
        for f in sorted(files):
            p = os.path.join(dirpath, f)
            if os.path.abspath(p) == out_abs:
                continue
            rel = os.path.relpath(p, root).replace(os.sep, "/")
            rows.append(f"{sha256_file(p)}  {rel}")
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(f"# {APP_NAME} v{__version__} SHA-256\n" + "\n".join(rows) + "\n")
    return len(rows)


def verify_install(root: str | None = None, manifest: str | None = None) -> dict:
    """Uygulama klasörünü derlemedeki parmak izleriyle karşılaştırır.

    Döndürür: {"ok": bool, "checked": int, "changed": [...], "missing": [...], "extra": [...],
               "no_manifest": bool}
    """
    root = root or app_dir()
    manifest = manifest or manifest_path(None if root == app_dir() else root)
    res = {"ok": False, "checked": 0, "changed": [], "missing": [], "extra": [], "no_manifest": False}
    try:
        known = read_manifest(manifest)
    except OSError:
        res["no_manifest"] = True
        return res
    man_rel = os.path.relpath(manifest, root).replace(os.sep, "/")
    seen = set()
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            p = os.path.join(dirpath, f)
            rel = os.path.relpath(p, root).replace(os.sep, "/")
            if rel == man_rel or f.endswith(ZONE):   # (Linux testlerinde akış ayrı dosya görünür)
                continue
            seen.add(rel)
            if rel not in known:
                res["extra"].append(rel)
                continue
            try:
                if sha256_file(p) != known[rel]:
                    res["changed"].append(rel)
            except OSError:
                res["changed"].append(rel)
            res["checked"] += 1
    res["missing"] = sorted(set(known) - seen)
    res["extra"].sort()
    res["changed"].sort()
    res["ok"] = not (res["changed"] or res["missing"])
    return res


def is_blocked(path: str) -> bool:
    try:
        with open(path + ZONE, "rb"):
            return True
    except (OSError, ValueError):
        return False


def blocked_files(root: str) -> list[str]:
    out = []
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            p = os.path.join(dirpath, f)
            if is_blocked(p):
                out.append(p)
    return out


def unblock(root: str, manifest: str | None = None) -> tuple[int, int, int]:
    """Windows'un "internetten indirildi" etiketini SADECE bizim dosyalarımızdan kaldırır.

    Bir dosya derlemedeki parmak iziyle birebir aynıysa etiketi kaldırılır.
    Değiştirilmiş ya da sonradan klasöre eklenmiş dosyalara dokunulmaz; Windows
    onlar için uyarmaya devam eder. (Özellikler > "Engellemeyi kaldır" ile aynı işlem.)
    Döndürür: (kaldırılan, başarısız, atlanan)
    """
    manifest = manifest or manifest_path(root)
    try:
        known = read_manifest(manifest)
    except OSError:
        known = None
    man_abs = os.path.normcase(os.path.abspath(manifest))
    ok = fail = skipped = 0
    for p in blocked_files(root):
        rel = os.path.relpath(p, root).replace(os.sep, "/")
        try:
            if known is not None and os.path.normcase(os.path.abspath(p)) == man_abs:
                os.remove(p + ZONE)          # listenin kendisi (düz metin)
                ok += 1
                continue
            if known is None or known.get(rel) != sha256_file(p):
                skipped += 1
                continue
            os.remove(p + ZONE)
            ok += 1
        except OSError:
            fail += 1
    return ok, fail, skipped


def should_offer_unblock() -> bool:
    """Sadece Windows'ta, paketlenmiş exe kendisi işaretliyse sorulur."""
    return sys.platform == "win32" and getattr(sys, "frozen", False) and is_blocked(sys.executable)


def unblock_status() -> dict | None:
    """Windows paketinde: {"exe": exe işaretli mi, "files": işaretli dosya sayısı}. Diğerlerinde None."""
    if not (sys.platform == "win32" and getattr(sys, "frozen", False)):
        return None
    return {"exe": is_blocked(sys.executable), "files": len(blocked_files(app_dir()))}


# ---------------------------------------------------------------- uyku engeli
_caffeinate = None


def keep_awake(on: bool) -> bool:
    """Kayıt sürerken bilgisayarın uykuya geçmesini engeller (ekran kapanabilir).

    Windows: SetThreadExecutionState (bağlantı kesilince / uygulama kapanınca kendiliğinden biter).
    macOS: "caffeinate" (uygulama kapanınca o da kapanır). Kalıcı hiçbir ayar değiştirilmez.
    Not: Kapak kapatma ve Windows Update'in yeniden başlatması bununla engellenemez.
    """
    global _caffeinate
    try:
        if sys.platform == "win32":
            import ctypes

            ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
            flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0)
            return bool(ctypes.windll.kernel32.SetThreadExecutionState(flags))
        if sys.platform == "darwin":
            import subprocess

            if on and _caffeinate is None:
                _caffeinate = subprocess.Popen(["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())])
            elif not on and _caffeinate is not None:
                _caffeinate.terminate()
                _caffeinate = None
            return True
    except Exception:  # noqa: BLE001
        return False
    return False


# ---------------------------------------------------------------- güncelleme kontrolü
def _vtuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3]) or (0,)


class _HttpsOnly(urllib.request.HTTPRedirectHandler):
    """Yönlendirme sadece https adreslerine izinli."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.lower().startswith("https://"):
            raise urllib.error.URLError("insecure redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


MAX_API_BYTES = 1024 * 1024


def check_latest(timeout: float = 6.0) -> dict:
    """GitHub'daki son sürümü okur (sadece kullanıcı isteyince çağrılır).

    Hiçbir şey indirilmez ya da çalıştırılmaz; sadece sürüm numarası okunur ve
    kullanıcı isterse tarayıcıda bu reponun Releases sayfası açılır.
    Döndürür: {"latest": "2.1.0", "url": ..., "newer": bool}
    """
    owner_repo = REPO_URL.rstrip("/").split("github.com/")[-1]
    req = urllib.request.Request(
        f"https://api.github.com/repos/{owner_repo}/releases/latest",
        headers={"Accept": "application/vnd.github+json", "User-Agent": f"{APP_NAME}/{__version__}"},
    )
    opener = urllib.request.build_opener(_HttpsOnly)
    with opener.open(req, timeout=timeout) as r:
        raw = r.read(MAX_API_BYTES + 1)
    if len(raw) > MAX_API_BYTES:
        raise ValueError("response too large")
    data = json.loads(raw.decode("utf-8"))
    tag = str(data.get("tag_name", "")).strip().lstrip("vV")
    if not re.fullmatch(r"\d+(?:\.\d+){0,3}", tag):
        raise ValueError("bad version")
    url = str(data.get("html_url") or "")
    if not url.startswith(f"{REPO_URL}/releases/"):     # sadece kendi Releases sayfamız açılır
        url = f"{REPO_URL}/releases/latest"
    return {"latest": tag, "url": url, "newer": _vtuple(tag) > _vtuple(__version__)}


# ---------------------------------------------------------------- hata bildirimi
def issue_url(lang: str = "tr") -> str:
    """GitHub'da yeni bir 'issue' sayfası açar; sürüm ve sistem bilgisi önceden doldurulur.

    Hiçbir şey otomatik gönderilmez: kullanıcı tarayıcıda görür, düzenler ve kendisi gönderir.
    """
    info = f"{APP_NAME} v{__version__} · {platform.system()} {platform.release()} · Python {platform.python_version()}"
    if lang == "tr":
        body = ("**Ne oldu / ne öneriyorsunuz?**\n\n\n"
                "**Nasıl tekrarlanır? (kart, baud, mod)**\n\n\n"
                f"---\n{info}")
    else:
        body = ("**What happened / what do you suggest?**\n\n\n"
                "**How to reproduce? (board, baud, mode)**\n\n\n"
                f"---\n{info}")
    q = urllib.parse.urlencode({"body": body})
    return f"{REPO_URL}/issues/new?{q}"
