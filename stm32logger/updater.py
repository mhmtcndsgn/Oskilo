"""Uygulama içi güncelleme.

Akış (Windows paketi):
1. GitHub'dan sürüm bilgisi okunur (test kanalı açıksa ön sürümler de dahil).
2. `Oskilo_Windows.zip` uygulama klasöründeki `_oskilo_new` klasörüne indirilir, sürümdeki
   `SHA256SUMS.txt` ile doğrulanır, güvenli biçimde açılır ve içindeki her dosya yeni sürümün
   parmak izi listesiyle (manifest) karşılaştırılır.
3. Windows çalışan programın dosyalarının üzerine yazdırmaz. Bu yüzden Oskilo kapanırken YENİ
   sürümün exe'si yardımcı olarak başlatılır (`--apply-update`). Yardımcı:
   - eski Oskilo'nun kapanmasını bekler; başka bir Oskilo penceresi açıksa güncellemeyi erteler,
   - her dosya işleminden ÖNCE işlemi günlüğe (`_oskilo_old/journal.jsonl`) yazar, sonra değişen
     dosyaların eskilerini `_oskilo_old`'a taşıyıp yenilerini kopyalar ("committed"),
   - yeni sürümü kısa bir deneme açılışıyla (`--update-probe`) sınar,
   - (yeniden başlatılıyorsa) yeni sürümü açar ve ana pencerenin kurulduğunu (`started.ok`) bekler.
   Herhangi bir adım başarısız olursa günlükten geri alınır ve eski sürüm açılır.
4. Eski dosyalar (yedek) ancak yeni sürümün açıldığı kanıtlanınca ("verified") ya da geri alma
   tamamlanınca silinir. Güncelleme yarıda kesilirse (elektrik, oturum kapatma) bir sonraki
   açılışta `preflight()` bunu görür ve geri almayı başlatır.

Ayarlar (kayıt defteri) ve kayıt dosyaları uygulama klasöründe olmadığı için etkilenmez.
macOS'ta .dmg indirilip doğrulanır ve açılır; diğer kurulumlarda sürüm sayfası açılır.
Bu modül sadece standart kütüphaneyi kullanır: yardımcı mod Qt yüklemeden çalışır.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from . import APP_NAME, REPO_URL, __version__
from .system import MANIFEST, MAX_API_BYTES, _HttpsOnly, _vtuple, app_dir, read_manifest, sha256_file

OWNER_REPO = REPO_URL.rstrip("/").split("github.com/")[-1]
API = f"https://api.github.com/repos/{OWNER_REPO}"
DOWNLOAD_PREFIX = f"{REPO_URL}/releases/download/"
ASSET_WIN = "Oskilo_Windows.zip"
ASSET_MAC = "Oskilo_macOS.dmg"
SUMS = "SHA256SUMS.txt"
TOP = "Oskilo"                       # zip içindeki üst klasör
NEW_DIR = "_oskilo_new"              # indirilen sürüm (uygulama klasörünün içinde)
OLD_DIR = "_oskilo_old"              # değiştirilen eski dosyalar ve işlem günlüğü (geri alma için)
TRASH = "_oskilo_trash"              # silinemeyen artıklar buraya taşınır, sonra silinir
# NEW_DIR içindeki işaretler
READY = "ready.json"                 # doğrulanmış, uygulanmaya hazır sürüm
STARTED = "started.ok"               # yeni sürüm ana penceresini kurdu
FAILED = "failed.txt"                # güncelleme başarısız (sonraki açılışta bildirilir)
APPLIED = "applied.txt"              # kapanışta uygulandı (sonraki açılışta "güncellendi" denir)
# OLD_DIR içindeki işaretler
JOURNAL = "journal.jsonl"            # her satır: [göreli_yol, işlem]; işlemden ÖNCE yazılır
COMMITTED = "committed"              # tüm dosyalar yerine kondu
VERIFIED = "verified"                # yeni sürüm açıldı: yedek silinebilir
ROLLEDBACK = "rolledback"            # geri alma tamamlandı: yedek silinebilir
ROLLING = "rolling"                  # geri alma başladı (yarıda kalırsa sonraki açılışta tamamlanır)
PROBED = "probed"                    # deneme açılışı geçti (doğrulama ilk gerçek açılışta)
HELPER_PID = "helper.pid"            # yardımcının süreç numarası (çalışırken başka açılış beklesin)
ATTEMPTS = "attempts"                # doğrulanmamış açılış denemeleri
MAX_ASSET = 600 * 1024 * 1024
MAX_UNZIP = 2 * 1024 * 1024 * 1024
MAX_FILES = 20000
MAX_SUMS = 64 * 1024
WATCH_S = 600                        # yeni sürümün açılmasını/kapanmasını bekleme süresi
PROBE_S = 90                         # deneme açılışı için süre
OTHERS_WAIT_S = 30                   # başka Oskilo pencerelerinin kapanmasını bekleme
CHUNK = 256 * 1024


class UpdateError(Exception):
    """Kullanıcıya gösterilecek hata. args[0] = çeviri anahtarı, kw = biçim değerleri."""

    def __init__(self, key: str, **kw):
        super().__init__(key)
        self.key = key
        self.kw = kw


class Cancelled(Exception):
    pass


# ================================================================ sürüm bilgisi
def _open(url: str, timeout: float, accept: str = "application/vnd.github+json"):
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": f"{APP_NAME}/{__version__}"})
    return urllib.request.build_opener(_HttpsOnly).open(req, timeout=timeout)


def _get_json(url: str, timeout: float):
    with _open(url, timeout) as r:
        raw = r.read(MAX_API_BYTES + 1)
    if len(raw) > MAX_API_BYTES:
        raise ValueError("response too large")
    return json.loads(raw.decode("utf-8"))


def parse_release(d) -> dict | None:
    """GitHub sürüm kaydını sadeleştirir. Geçersiz/taslak sürümler için None."""
    if not isinstance(d, dict) or d.get("draft"):
        return None
    tag = str(d.get("tag_name", "")).strip()
    ver = tag.lstrip("vV")
    if not re.fullmatch(r"\d+\.\d+\.\d+", ver):
        return None
    url = str(d.get("html_url") or "")
    if not url.startswith(f"{REPO_URL}/releases/"):
        url = f"{REPO_URL}/releases/tag/{tag}"
    assets = {}
    for a in d.get("assets") or []:
        if not isinstance(a, dict):
            continue
        name, link = str(a.get("name", "")), str(a.get("browser_download_url", ""))
        if name and link.startswith(DOWNLOAD_PREFIX):        # sadece bu reponun sürüm dosyaları
            try:
                size = int(a.get("size") or 0)
            except (TypeError, ValueError):
                size = 0
            assets[name] = {"url": link, "size": size}
    return {"version": ver, "tag": tag, "url": url, "prerelease": bool(d.get("prerelease")), "assets": assets}


def find_update(beta: bool = False, timeout: float = 8.0, current: str = __version__) -> dict:
    """Son sürüm. beta=True ise ön (test) sürümler de dahil, en yüksek sürüm seçilir.

    Döndürür: {"latest", "url", "newer", "prerelease", "assets", "tag"}
    """
    if beta:
        data = _get_json(f"{API}/releases?per_page=20", timeout)
        rels = [r for r in (parse_release(x) for x in (data if isinstance(data, list) else [])) if r]
        if not rels:
            raise ValueError("no releases")
        best = max(rels, key=lambda r: _vtuple(r["version"]))
    else:
        best = parse_release(_get_json(f"{API}/releases/latest", timeout))
        if best is None:
            raise ValueError("bad version")
    return {"latest": best["version"], "url": best["url"], "tag": best["tag"],
            "newer": _vtuple(best["version"]) > _vtuple(current), "prerelease": best["prerelease"],
            "assets": best["assets"]}


# ================================================================ kurulum türü
def install_dir() -> Path:
    """Paketlenmiş uygulamada exe'nin klasörü (kaynak koddan çalışırken proje klasörü)."""
    return Path(app_dir())


def exe_name() -> str:
    return f"{APP_NAME}.exe" if sys.platform == "win32" else APP_NAME


def manifest_of(root: Path) -> Path:
    return Path(root) / "_internal" / MANIFEST


def manifest_version(path: Path) -> str | None:
    """Manifest'in ilk satırındaki sürüm: '# Oskilo v3.2.0 SHA-256'."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            m = re.match(rf"#\s*{re.escape(APP_NAME)}\s+v(\d+\.\d+\.\d+)", f.readline())
        return m.group(1) if m else None
    except OSError:
        return None


def mode() -> str:
    """'replace' (Windows paketi: kendini günceller), 'dmg' (macOS) ya da 'page' (sürüm sayfası)."""
    if not getattr(sys, "frozen", False):
        return "page"
    if sys.platform == "win32" and manifest_of(install_dir()).exists():
        return "replace"
    if sys.platform == "darwin":
        return "dmg"
    return "page"


# ================================================================ indirme ve doğrulama
def download(url: str, dest: Path, expected_size: int = 0, progress=None, cancel=None, timeout: float = 30.0) -> Path:
    """HTTPS ile indirir (yönlendirmeler de HTTPS). Önce .part dosyasına yazar."""
    if not url.startswith(DOWNLOAD_PREFIX):
        raise UpdateError("upd_e_pkg")
    dest = Path(dest)
    part = dest.with_name(dest.name + ".part")
    done = 0
    try:
        with _open(url, timeout, accept="application/octet-stream") as r, open(part, "wb") as f:
            total = int(r.headers.get("Content-Length") or expected_size or 0)
            if total > MAX_ASSET:
                raise UpdateError("upd_e_pkg")
            while True:
                if cancel is not None and cancel():
                    raise Cancelled()
                block = r.read(CHUNK)
                if not block:
                    break
                done += len(block)
                if done > MAX_ASSET:
                    raise UpdateError("upd_e_pkg")
                f.write(block)
                if progress is not None:
                    progress(done, total)
        if expected_size and done != expected_size:
            raise UpdateError("upd_e_net", err=f"{done} / {expected_size} bytes")
        os.replace(part, dest)
        return dest
    except (urllib.error.URLError, http.client.HTTPException, OSError) as e:
        raise UpdateError("upd_e_net", err=getattr(e, "reason", e)) from e
    finally:
        if part.exists():
            try:
                part.unlink()
            except OSError:
                pass


def parse_sums(text: str) -> dict[str, str]:
    out = {}
    for ln in text.splitlines():
        m = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?(\S+)\s*", ln)
        if m:
            out[m.group(2)] = m.group(1).lower()
    return out


def fetch_sums(rel: dict, timeout: float = 15.0) -> dict[str, str]:
    a = rel["assets"].get(SUMS)
    if not a:
        raise UpdateError("upd_e_asset", name=SUMS)
    try:
        with _open(a["url"], timeout, accept="application/octet-stream") as r:
            raw = r.read(MAX_SUMS + 1)
    except (urllib.error.URLError, http.client.HTTPException, OSError) as e:
        raise UpdateError("upd_e_net", err=getattr(e, "reason", e)) from e
    if len(raw) > MAX_SUMS:
        raise UpdateError("upd_e_pkg")
    return parse_sums(raw.decode("utf-8", "replace"))


def extract_safe(zip_path: Path, dest: Path, top: str = TOP, cancel=None) -> Path:
    """Zip'i açar. Üst klasör dışına çıkan, mutlak, '..' içeren ya da bağlantı (symlink) olan
    girdiler, aşırı büyük/çok dosyalı arşivler reddedilir."""
    dest = Path(dest)
    with zipfile.ZipFile(zip_path) as zf:
        infos = zf.infolist()
        if len(infos) > MAX_FILES:
            raise UpdateError("upd_e_pkg")
        total = 0
        for i in infos:
            name = i.filename.replace("\\", "/")
            parts = [p for p in name.split("/") if p]
            if (not parts or name.startswith("/") or re.match(r"^[A-Za-z]:", name) or ".." in parts
                    or parts[0] != top):
                raise UpdateError("upd_e_pkg")
            if (i.external_attr >> 16) & 0o170000 == 0o120000:     # symlink
                raise UpdateError("upd_e_pkg")
            total += i.file_size
            if total > MAX_UNZIP:
                raise UpdateError("upd_e_pkg")
        root = (dest / top).resolve()
        for i in infos:
            if cancel is not None and cancel():
                raise Cancelled()
            out = (dest / i.filename.replace("\\", "/")).resolve()
            if out != root and root not in out.parents:
                raise UpdateError("upd_e_pkg")
            zf.extract(i, dest)
    return dest / top


def verify_tree(root: Path, expect_version: str, cancel=None) -> dict[str, str]:
    """Açılan yeni sürüm: manifest'teki sürüm beklenen sürüm mü, her dosya parmak iziyle aynı mı?"""
    man = manifest_of(root)
    if manifest_version(man) != expect_version:
        raise UpdateError("upd_e_pkg")
    known = read_manifest(str(man))
    if not known or exe_name() not in known:
        raise UpdateError("upd_e_pkg")
    for rel, digest in known.items():
        if cancel is not None and cancel():
            raise Cancelled()
        p = root / rel
        if not p.is_file() or sha256_file(str(p)) != digest:
            raise UpdateError("upd_e_sha")
    return known



# ================================================================ klasör yardımcıları
def _on_rm_error(func, path, _exc):
    """Salt okunur dosyalar Windows'ta silinemez: özniteliği kaldırıp bir kez daha dener."""
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        func(path)
    except OSError:
        pass


def _force_rmtree(p: Path) -> bool:
    """Klasörü siler; silinemeyen artık kalırsa onu çöp klasörüne taşır. True = yol artık boş."""
    p = Path(p)
    if not p.exists():
        return True
    try:
        shutil.rmtree(p, onerror=_on_rm_error)
    except OSError:
        pass
    if not p.exists():
        return True
    try:                                               # kilitli bir artık: adını değiştir, sonra silinir
        p.rename(p.parent / f"{TRASH}_{int(time.time() * 1000)}_{os.getpid()}")
        return True
    except OSError:
        return False


def _fresh_dir(p: Path, root: Path) -> None:
    if not _force_rmtree(p):
        raise UpdateError("upd_e_perm", dir=str(root))
    try:
        p.mkdir()
    except OSError as e:
        raise UpdateError("upd_e_perm", dir=str(root)) from e


def _clean_trash(root: Path) -> None:
    for d in Path(root).glob(f"{TRASH}_*"):
        shutil.rmtree(d, onerror=_on_rm_error)


def _write(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())


def _read(path: Path, default: str = "") -> str:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return default


# ================================================================ hazırlık (eski sürüm, arka planda)
def prepare_windows(rel: dict, root: Path | None = None, progress=None, cancel=None) -> Path:
    """Yeni sürümü indirir, doğrular, açar. Döndürür: yardımcı olarak çalıştırılacak yeni exe."""
    root = Path(root or install_dir())
    if not manifest_of(root).exists():
        raise UpdateError("upd_e_perm", dir=str(root))
    asset = rel["assets"].get(ASSET_WIN)
    if not asset:
        raise UpdateError("upd_e_asset", name=ASSET_WIN)
    staging = root / NEW_DIR
    _fresh_dir(staging, root)
    try:
        probe = staging / "yazma_testi.tmp"
        probe.write_bytes(b"ok")
        probe.unlink()
    except OSError as e:
        _force_rmtree(staging)
        raise UpdateError("upd_e_perm", dir=str(root)) from e
    need = max(asset["size"], 50 * 1024 * 1024) * 5     # zip + açılmış hali + yedek için pay
    try:
        free = shutil.disk_usage(root).free
    except OSError:
        free = need
    if free < need:
        _force_rmtree(staging)
        raise UpdateError("upd_e_space", mb=need // (1024 * 1024))
    try:
        if progress:
            progress(0, asset["size"], "dl")
        sums = fetch_sums(rel)
        digest = sums.get(ASSET_WIN)
        if not digest:
            raise UpdateError("upd_e_asset", name=SUMS)
        zpath = staging / ASSET_WIN
        download(asset["url"], zpath, asset["size"],
                 (lambda d, t: progress(d, t, "dl")) if progress else None, cancel)
        if progress:
            progress(0, 0, "verify")
        if sha256_file(str(zpath)) != digest:
            raise UpdateError("upd_e_sha")
        if cancel is not None and cancel():
            raise Cancelled()
        if progress:
            progress(0, 0, "extract")
        new_root = extract_safe(zpath, staging, cancel=cancel)
        zpath.unlink()
        verify_tree(new_root, rel["version"], cancel)
        _write(staging / READY, json.dumps({"version": rel["version"],
                                           "manifest": sha256_file(str(manifest_of(new_root)))}))
        return new_root / exe_name()
    except BaseException:
        _force_rmtree(staging)
        raise


def download_dmg(rel: dict, progress=None, cancel=None) -> Path:
    """macOS: .dmg'yi İndirilenler klasörüne indirir ve doğrular (açmak arayüzün işi)."""
    asset = rel["assets"].get(ASSET_MAC)
    if not asset:
        raise UpdateError("upd_e_asset", name=ASSET_MAC)
    sums = fetch_sums(rel)
    digest = sums.get(ASSET_MAC)
    if not digest:
        raise UpdateError("upd_e_asset", name=SUMS)
    folder = Path.home() / "Downloads"
    try:                                                     # izin yoksa (macOS gizlilik ayarı) geçici klasör
        probe = folder / ".oskilo_yazma_testi"
        probe.write_bytes(b"")
        probe.unlink()
    except OSError:
        import tempfile

        folder = Path(tempfile.gettempdir())
    dest = folder / f"Oskilo_macOS_v{rel['version']}.dmg"
    n = 2
    while dest.exists():
        dest = folder / f"Oskilo_macOS_v{rel['version']} ({n}).dmg"
        n += 1
    download(asset["url"], dest, asset["size"], (lambda d, t: progress(d, t, "dl")) if progress else None, cancel)
    if sha256_file(str(dest)) != digest:
        try:
            dest.unlink()
        except OSError:
            pass
        raise UpdateError("upd_e_sha")
    return dest


def pending(root: Path | None = None, current: str = __version__) -> dict | None:
    """Daha önce indirilip doğrulanmış, henüz uygulanmamış güncelleme: {"version", "helper"}."""
    root = Path(root or install_dir())
    staging = root / NEW_DIR
    try:
        info = json.loads((staging / READY).read_text(encoding="utf-8"))
        ver, man_sha = str(info["version"]), str(info["manifest"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    helper = staging / TOP / exe_name()
    man = manifest_of(staging / TOP)
    try:
        ok = (_vtuple(ver) > _vtuple(current) and helper.is_file() and manifest_version(man) == ver
              and sha256_file(str(man)) == man_sha and not (staging / FAILED).exists())
    except OSError:
        ok = False
    return {"version": ver, "helper": helper} if ok else None


# ================================================================ süreçler
def _cmd_for(exe: Path) -> list[str]:
    """Paketlenmiş uygulamada exe'nin kendisi; kaynak koddan çalışırken (testler) python main.py."""
    if getattr(sys, "frozen", False):
        return [str(exe)]
    return [sys.executable, str(Path(exe).parent / "main.py")]


def _spawn(cmd: list[str], cwd: Path) -> subprocess.Popen:
    kw = {"cwd": str(cwd), "close_fds": True, "stdin": subprocess.DEVNULL,
          "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if sys.platform != "win32":
        return subprocess.Popen(cmd, start_new_session=True, **kw)
    base = 0x00000008 | 0x00000200                      # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    try:                                                # başlatan "iş" (job) kapanınca birlikte ölmesin
        return subprocess.Popen(cmd, creationflags=base | 0x01000000, **kw)   # CREATE_BREAKAWAY_FROM_JOB
    except OSError:
        return subprocess.Popen(cmd, creationflags=base, **kw)


def _allow_foreground() -> None:
    """Yeniden açılan pencere diğer pencerelerin arkasında kalmasın."""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.AllowSetForegroundWindow(-1)    # ASFW_ANY
        except Exception:  # noqa: BLE001
            pass


def _k32():
    import ctypes

    k32 = ctypes.windll.kernel32
    k32.OpenProcess.restype = ctypes.c_void_p
    k32.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
    k32.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
    k32.WaitForSingleObject.restype = ctypes.c_uint32
    k32.CloseHandle.argtypes = (ctypes.c_void_p,)
    return k32


def wait_pid(pid: int, timeout: float) -> bool:
    """pid'li süreç bitene kadar bekler. True = bitti (ya da hiç yoktu)."""
    if pid <= 0:
        return True
    if sys.platform == "win32":
        k32 = _k32()
        h = k32.OpenProcess(0x00100000, False, pid)          # SYNCHRONIZE
        if not h:
            return True
        try:
            return k32.WaitForSingleObject(h, int(max(0.0, timeout) * 1000)) == 0
        finally:
            k32.CloseHandle(h)
    end = time.monotonic() + timeout
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            pass
        try:                                                  # kendi çocuğumuzsa zombi kalmasın
            if os.waitpid(pid, os.WNOHANG)[0] == pid:
                return True
        except ChildProcessError:
            pass
        if time.monotonic() >= end:
            return False
        time.sleep(0.2)


def pid_alive(pid: int) -> bool:
    return pid > 0 and pid != os.getpid() and not wait_pid(pid, 0)


def _image_of(pid: int) -> str | None:
    """Windows: sürecin exe yolu (bilinmiyorsa None)."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes as w

    try:
        k32 = _k32()
        k32.QueryFullProcessImageNameW.argtypes = (ctypes.c_void_p, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD))
        h = k32.OpenProcess(0x1000, False, pid)                # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return None
        try:
            buf = ctypes.create_unicode_buffer(32768)
            size = w.DWORD(32768)
            return buf.value if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)) else None
        finally:
            k32.CloseHandle(h)
    except Exception:  # noqa: BLE001
        return None


def _canon(p) -> str:
    """Karşılaştırma için tam yol: kısa (8.3) adlar, eşlenmiş/SUBST sürücüler, büyük-küçük harf."""
    try:
        return os.path.normcase(os.path.realpath(str(p)))
    except (OSError, ValueError):
        return os.path.normcase(os.path.abspath(str(p)))


def helper_alive(pid: int, root: Path) -> bool:
    """Güncelleme yardımcısı hâlâ çalışıyor mu? (Windows'ta süreç numarası başka programa geçmiş
    olabilir: exe yolunun indirme klasöründe olduğu da kontrol edilir.)"""
    if not pid_alive(pid):
        return False
    img = _image_of(pid)
    if img is None:
        return True
    return _canon(img).startswith(_canon(Path(root) / NEW_DIR) + os.sep)


def other_instances(root: Path, exclude: set[int]) -> list[int]:
    """Windows: bu klasördeki Oskilo.exe'den çalışan diğer süreçler."""
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes as w

    try:
        psapi = ctypes.WinDLL("psapi")
        arr = (w.DWORD * 8192)()
        needed = w.DWORD()
        if not psapi.EnumProcesses(arr, ctypes.sizeof(arr), ctypes.byref(needed)):
            return []
        target = _canon(Path(root) / exe_name())
        found = []
        for pid in arr[:needed.value // ctypes.sizeof(w.DWORD)]:
            if not pid or pid in exclude or pid == os.getpid():
                continue
            img = _image_of(int(pid))
            if img is not None and _canon(img) == target:
                found.append(int(pid))
        return found
    except Exception:  # noqa: BLE001
        return []


def launch_helper(helper_exe: Path, root: Path | None = None, restart: bool = True) -> None:
    """Eski sürüm kapanırken çağrılır: yeni sürümün exe'sini yardımcı olarak başlatır."""
    root = Path(root or install_dir())
    helper_exe = Path(helper_exe)
    cmd = _cmd_for(helper_exe) + ["--apply-update", str(root), "--src", str(helper_exe.parent),
                                  "--wait-pid", str(os.getpid())]
    if restart:
        cmd.append("--restart")
    _allow_foreground()
    _spawn(cmd, helper_exe.parent)


# ================================================================ günlük
def update_log_path() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "Oskilo"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Logs" / "Oskilo"
    else:
        base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "oskilo"
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError:
        base = Path.home()
    return base / "oskilo_guncelleme.txt"


class _Log:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or update_log_path())
        try:
            if self.path.exists() and self.path.stat().st_size > 256 * 1024:
                self.path.unlink()
        except OSError:
            pass

    def __call__(self, msg: str) -> None:
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  [{os.getpid()}] {msg}\n")
        except OSError:
            pass


def _notify(text: str) -> None:
    """Qt olmadan kullanıcıya mesaj (Windows mesaj kutusu)."""
    if os.environ.get("OSKILO_UPDATE_NO_UI") == "1":        # otomatik testlerde pencere bekletmesin
        _Log()(f"(mesaj) {text!r}")
        return
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, text, APP_NAME, 0x40 | 0x10000 | 0x40000)   # bilgi, en önde
        except Exception:  # noqa: BLE001
            pass


# ================================================================ dosyaları değiştirme ve geri alma
def _retry(fn, *a, tries: int = 40, delay: float = 0.5):
    """Antivirüs/OneDrive dosyayı kısa süre tutabilir: birkaç kez dener."""
    for k in range(tries):
        try:
            return fn(*a)
        except FileNotFoundError:
            raise
        except OSError:
            if k == tries - 1:
                raise
            time.sleep(delay)


def _man_rel() -> str:
    return f"_internal/{MANIFEST}"


class _Journal:
    """İşlem günlüğü: her satır işlemden ÖNCE yazılır ve diske zorlanır."""

    def __init__(self, path: Path):
        self.f = open(path, "a", encoding="utf-8")

    def add(self, rel: str, kind: str) -> None:
        self.f.write(json.dumps([rel, kind]) + "\n")
        self.f.flush()
        os.fsync(self.f.fileno())

    def close(self) -> None:
        try:
            self.f.close()
        except OSError:
            pass


def read_journal(backup: Path) -> list[list[str]] | None:
    """None = günlük yok (hiçbir işlem yapılmadı). Okunamayan günlük (kilitli) hata olarak yükselir."""
    try:
        lines = (Path(backup) / JOURNAL).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return None
    out = []
    for ln in lines:
        try:
            rel, kind = json.loads(ln)
        except (ValueError, TypeError):
            continue                                       # yarım kalmış son satır
        out.append([str(rel), str(kind)])
    return out


def _mkdirs(root: Path, d: Path, j: _Journal) -> None:
    """Eksik klasörleri oluşturur ve günlüğe yazar (geri almada boşsa silinir)."""
    missing = []
    while d != root and not d.exists():
        missing.append(d)
        d = d.parent
    for m in reversed(missing):
        j.add(m.relative_to(root).as_posix(), "mkdir")
        m.mkdir()


def apply(src: Path, root: Path, log) -> bool:
    """Yeni sürümün dosyalarını root'a koyar. Hata olursa günlükten geri alır. True = başarılı."""
    src, root = Path(src), Path(root)
    backup = root / OLD_DIR
    j = None
    try:
        old_known = read_manifest(str(manifest_of(root)))
        new_known = read_manifest(str(manifest_of(src)))
        if not new_known or exe_name() not in new_known:
            raise UpdateError("upd_e_pkg")
        _fresh_dir(backup, root)
        _write(backup / HELPER_PID, str(os.getpid()))
        j = _Journal(backup / JOURNAL)
        norm_new = {os.path.normcase(r) for r in new_known}
        changed = 0

        def to_backup(rel: str, kind: str) -> None:
            t, b = root / rel, backup / rel
            b.parent.mkdir(parents=True, exist_ok=True)
            j.add(rel, kind)
            _retry(os.replace, t, b)

        old_only = [r for r in sorted(set(old_known) - {_man_rel()})
                    if os.path.normcase(r) not in norm_new and (root / r).is_file()]
        new_dirs = {os.path.normcase(str(Path(r).parent.as_posix())) for r in new_known}
        new_dirs |= {os.path.normcase(str(p.as_posix())) for r in new_known for p in Path(r).parents}
        # 1) önce yol çakışmaları: yeni sürümde klasör olacak yerdeki eski dosyalar ve yeni sürümde
        #    dosya olacak klasörün içindeki eski dosyalar
        def conflicts(r: str) -> bool:
            return (os.path.normcase(r) in new_dirs
                    or any(os.path.normcase(p.as_posix()) in norm_new for p in Path(r).parents))

        for rel in [r for r in old_only if conflicts(r)]:
            to_backup(rel, "removed")
            old_only.remove(rel)
            changed += 1
        # 2) yeni ve değişen dosyalar. Python çekirdeği ve exe en sona (yarıda kalırsa eski exe eski
        #    Python'la açılabilsin ve yarım işi görüp geri alsın), manifest en en sona.
        exe = exe_name()
        core = re.compile(r"_internal/(base_library\.zip|python3\d*\.dll|libpython3[\d.]*\.(so[\d.]*|dylib))$", re.I)
        late = sorted(r for r in new_known if core.match(r))
        order = (sorted(r for r in new_known if r not in (_man_rel(), exe) and r not in late)
                 + late + [exe, _man_rel()])
        for rel in order:
            s, t = src / rel, root / rel
            if t.is_file() and rel != _man_rel() and sha256_file(str(t)) == new_known[rel]:
                continue                                       # değişmemiş: dokunma
            _mkdirs(root, t.parent, j)
            if t.is_dir():                                     # eskiden klasördü (boş kalmış olmalı)
                j.add(rel, "rmdir")
                t.rmdir()
            if t.exists():
                to_backup(rel, "replaced")
            else:
                j.add(rel, "added")
            _retry(shutil.copy2, s, t)                       # izinler de (macOS/Linux: çalıştırılabilir)
            if rel != _man_rel() and sha256_file(str(t)) != new_known[rel]:
                raise UpdateError("upd_e_sha")
            changed += 1
        # 3) kalan eski dosyalar en son (ör. eski Python DLL'i, eski exe'nin ihtiyacı kalmayınca)
        for rel in old_only:
            to_backup(rel, "removed")
            changed += 1
        j.close()
        _write(backup / COMMITTED, str(changed))
        # boş kalan eski klasörler
        for rel in sorted(set(old_known) - set(new_known), key=lambda r: -r.count("/")):
            d = (root / rel).parent
            while d != root and root in d.parents:
                try:
                    d.rmdir()
                except OSError:
                    break
                d = d.parent
        log(f"uygulandı: {changed} dosya değişti")
        return True
    except BaseException as e:  # noqa: BLE001
        log(f"HATA, geri alınıyor: {type(e).__name__}: {e}")
        if j is not None:
            j.close()
        if j is not None and not (backup / COMMITTED).exists():     # sadece bu çalışmanın günlüğü
            rollback(root, log)
        return False


def rollback(root: Path, log) -> bool:
    """Günlükteki işlemleri tersten geri alır. Tamamlanırsa ROLLEDBACK işareti bırakır."""
    root = Path(root)
    backup = root / OLD_DIR
    try:
        journal = read_journal(backup)
    except OSError as e:                    # ör. antivirüs kısa süre tutuyor: sonraki açılış yeniden dener
        log(f"geri alma günlüğü okunamadı: {e}")
        return False
    if journal is None:                     # günlük her işlemden önce yazılır: günlük yoksa hiçbir şeye dokunulmadı
        log("geri alma günlüğü yok: hiçbir dosya değişmemişti")
        try:
            backup.mkdir(exist_ok=True)
            _write(backup / ROLLEDBACK, "no-journal")
        except OSError:
            pass
        return True
    _write(backup / ROLLING, "1")           # yarıda kalırsa sonraki açılış tamamlar
    for m in (COMMITTED, VERIFIED, PROBED):
        try:
            (backup / m).unlink()
        except OSError:
            pass
    ok = True
    for rel, kind in reversed(journal):
        t, b = root / rel, backup / rel
        try:
            if kind in ("replaced", "removed"):
                if b.exists():                       # işlem yapılmamışsa yedek yoktur: dosya zaten eski
                    if t.is_dir():
                        t.rmdir()
                    t.parent.mkdir(parents=True, exist_ok=True)
                    _retry(os.replace, b, t)
            elif kind == "added":
                if t.is_file():
                    _retry(os.remove, t)
            elif kind == "mkdir":
                if t.is_dir() and not any(t.iterdir()):
                    t.rmdir()
            elif kind == "rmdir":
                t.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            ok = False
            log(f"geri alınamadı: {rel}: {e}")
    if ok:
        _write(backup / ROLLEDBACK, "ok")
        try:
            (backup / ROLLING).unlink()
        except OSError:
            pass
    log("geri alma " + ("tamam" if ok else "EKSİK"))
    return ok


def _arg(argv: list[str], name: str, default=None):
    if name in argv:
        i = argv.index(name)
        if len(argv) > i + 1:
            return argv[i + 1]
    return default


def _mark_failed(staging: Path, msg: str) -> None:
    try:
        staging.mkdir(exist_ok=True)
        _write(staging / FAILED, msg)
        (staging / READY).unlink(missing_ok=True)
    except OSError:
        pass


def _probe(root: Path, log) -> bool:
    """Yeni sürüm açılabiliyor mu? Qt ve ana pencere modülleri yüklenip çıkılır (pencere açılmaz)."""
    try:
        p = _spawn(_cmd_for(root / exe_name()) + ["--update-probe"], root)
    except OSError as e:
        log(f"deneme açılışı başlatılamadı: {e}")
        return False
    try:
        rc = p.wait(PROBE_S)
    except subprocess.TimeoutExpired:
        p.kill()
        log("deneme açılışı zaman aşımı")
        return False
    log(f"deneme açılışı: kod {rc}")
    return rc == 0


def apply_main(argv: list[str]) -> int:
    """`Oskilo.exe --apply-update <kurulum> --src <yeni> --wait-pid <pid> [--restart]`"""
    log = _Log()
    root = Path(_arg(argv, "--apply-update", "") or ".")
    try:
        return _apply_main(argv, root, log)
    except BaseException as e:  # noqa: BLE001  beklenmeyen hata: yarım işi geri al, eski sürümü aç
        log(f"BEKLENMEYEN HATA: {type(e).__name__}: {e}")
        backup = root / OLD_DIR
        ok = True
        ours = _read(backup / HELPER_PID) == str(os.getpid())      # günlük bu çalışmanın mı?
        if (ours and (backup / JOURNAL).exists() and not (backup / ROLLEDBACK).exists()
                and not (backup / VERIFIED).exists()):
            try:
                ok = rollback(root, log)
            except BaseException as e2:  # noqa: BLE001
                log(f"geri alma da başarısız: {e2}")
                ok = False
        _mark_failed(root / NEW_DIR, "beklenmeyen hata")
        if "--restart" in argv and ok:
            try:
                _spawn(_cmd_for(root / exe_name()) + ["--update-failed"], root)
            except OSError:
                pass
        return 1


def _apply_main(argv: list[str], root: Path, log) -> int:
    src = Path(_arg(argv, "--src", "") or ".")
    restart = "--restart" in argv
    try:
        pid = int(_arg(argv, "--wait-pid", "0"))
    except ValueError:
        pid = 0
    log(f"===== güncelleme v{__version__}: {src} -> {root} (yeniden başlat={restart})")
    staging = root / NEW_DIR
    backup = root / OLD_DIR
    old_ver = manifest_version(manifest_of(root)) or "?"
    old_exe = root / exe_name()
    # güvenlik: hedef bir Oskilo klasörü, kaynak onun içindeki doğrulanmış indirme olmalı
    info: dict = {}
    try:
        valid = (manifest_of(root).is_file() and manifest_of(src).is_file()
                 and _canon(src.parent) == _canon(staging))
        if valid:
            info = json.loads((staging / READY).read_text(encoding="utf-8"))
            valid = sha256_file(str(manifest_of(src))) == info.get("manifest")
    except (OSError, ValueError, AttributeError):
        valid = False
    if not valid:
        log("geçersiz ya da doğrulanmamış klasörler, çıkılıyor")
        return 2

    def relaunch_old(flag: str | None) -> None:
        if restart:
            try:
                _spawn(_cmd_for(old_exe) + ([flag] if flag else []), root)
            except OSError as e:
                log(f"eski sürüm açılamadı: {e}")

    def fail(msg: str, rolled_back: bool = True) -> int:
        _mark_failed(staging, msg)
        if rolled_back:
            relaunch_old("--update-failed")
        else:
            _notify("Oskilo güncellemesi yarıda kaldı ve otomatik geri alınamadı.\n"
                    "Oskilo'yu yeniden açtığınızda tekrar denenecek. Olmazsa Oskilo'yu GitHub'dan yeniden indirin.\n\n"
                    "The Oskilo update could not be completed or undone. It will be retried when you open Oskilo;\n"
                    f"if that does not help, download Oskilo again from GitHub.\n\n{REPO_URL}/releases\n\nLog: {log.path}")
        return 4

    if not wait_pid(pid, 90):
        log(f"eski süreç ({pid}) kapanmadı; hiçbir şey değişmedi, güncelleme bekliyor")
        return 3
    time.sleep(0.5)
    try:
        verify_tree(src, str(info.get("version", "")))
    except UpdateError as e:
        log(f"indirilen dosyalar artık doğrulanamıyor ({e.key}), vazgeçildi")
        _force_rmtree(staging)
        relaunch_old(None)
        return 5
    others = other_instances(root, {pid})
    end = time.monotonic() + OTHERS_WAIT_S
    while others and time.monotonic() < end:
        time.sleep(1)
        others = other_instances(root, {pid})
    if others:
        log(f"başka Oskilo pencereleri açık ({others}), güncelleme ertelendi")
        if restart:                                  # açılan eski sürüm durumu kendi penceresinde söyler
            relaunch_old("--update-postponed")
        else:
            _notify("Başka bir Oskilo penceresi açık olduğu için güncelleme ertelendi.\n"
                    "Tüm Oskilo pencerelerini kapatıp Oskilo'yu yeniden açın; güncelleme hazır bekliyor.\n\n"
                    "Another Oskilo window is open, so the update was postponed.\n"
                    "Close all Oskilo windows and open Oskilo again; the update is ready.")
        return 6

    if not apply(src, root, log):
        touched = _read(backup / HELPER_PID) == str(os.getpid())  # apply günlüğe geldi mi?
        complete = not touched or (backup / ROLLEDBACK).exists()
        return fail("dosyalar değiştirilemedi", rolled_back=complete)
    if not _probe(root, log):
        ok = rollback(root, log)
        return fail("yeni sürüm açılamadı (deneme)", rolled_back=ok)
    if not restart:
        _write(backup / PROBED, "1")                 # doğrulama ilk gerçek açılışta (mark_started)
        _write(staging / APPLIED, old_ver)
        log("tamam (yeniden başlatılmadı; ilk açılışta doğrulanacak)")
        return 0
    try:
        proc = _spawn(_cmd_for(root / exe_name()) + ["--updated-from", old_ver, "--update-helper",
                                                    str(os.getpid())], root)
    except OSError as e:
        log(f"yeni sürüm başlatılamadı: {e}")
        ok = rollback(root, log)
        return fail("yeni sürüm başlatılamadı", rolled_back=ok)
    end = time.monotonic() + WATCH_S
    while time.monotonic() < end:
        if (staging / STARTED).exists():
            _write(backup / VERIFIED, "started")
            log("yeni sürüm açıldı, tamam")
            return 0
        rc = proc.poll()
        if rc is not None:
            if rc == 0:                                  # kullanıcı hemen kapattı ya da "zaten açık"ta vazgeçti
                _write(backup / VERIFIED, "exit0")
                log("yeni sürüm normal kapandı, tamam")
                return 0
            log(f"yeni sürüm açılış işaretinden önce kapandı (kod {rc}), geri alınıyor")
            ok = rollback(root, log)
            return fail("yeni sürüm açılamadı", rolled_back=ok)
        time.sleep(0.25)
    log("yeni sürüm uzun süredir açılıyor; çalışan uygulamaya dokunulmadı (ilk açılışta doğrulanacak)")
    return 0


def rollback_main(argv: list[str]) -> int:
    """`Oskilo.exe --rollback-update <kurulum> --wait-pid <pid>`: yarıda kalmış güncellemeyi geri alır.
    İndirme klasöründeki exe'den çalışır (kurulumdaki exe'nin yerine eskisi konabilsin diye)."""
    log = _Log()
    root = Path(_arg(argv, "--rollback-update", "") or ".")
    try:
        pid = int(_arg(argv, "--wait-pid", "0"))
    except ValueError:
        pid = 0
    log(f"===== geri alma: {root}")
    backup = root / OLD_DIR
    if not backup.is_dir():
        log("yedek klasörü yok, çıkılıyor")
        return 2
    try:
        _write(backup / HELPER_PID, str(os.getpid()))   # bu sırada açılan Oskilo "güncelleniyor" desin
    except OSError:
        pass
    wait_pid(pid, 60)
    time.sleep(0.5)
    staging = root / NEW_DIR
    if rollback(root, log):
        _mark_failed(staging, "yarıda kalan güncelleme geri alındı")
        try:
            _spawn(_cmd_for(root / exe_name()) + ["--update-failed"], root)
        except OSError as e:
            log(f"eski sürüm açılamadı: {e}")
        return 0
    _notify("Oskilo güncellemesi yarıda kaldı ve otomatik geri alınamadı.\n"
            "Oskilo'yu yeniden açtığınızda tekrar denenecek. Olmazsa Oskilo'yu GitHub'dan yeniden indirin.\n\n"
            "The Oskilo update could not be undone automatically. It will be retried when you open Oskilo;\n"
            f"if that does not help, download Oskilo again from GitHub.\n\n{REPO_URL}/releases\n\nLog: {log.path}")
    return 4


# ================================================================ açılış (main.py, Qt'den önce)
def preflight(argv: list[str], root: Path | None = None) -> str | None:
    """Uygulama açılırken, Qt yüklenmeden önce. Döndürür:
    None: normal devam · "busy": güncelleme sürüyor, çık · "delegated": geri alma başlatıldı, çık ·
    "broken": geri alınamadı, kullanıcıya söylendi."""
    root = Path(root or install_dir())
    backup = root / OLD_DIR
    if not backup.is_dir() or (backup / VERIFIED).exists() or (backup / ROLLEDBACK).exists():
        return None
    try:
        helper = int(_read(backup / HELPER_PID, "0") or 0)
    except ValueError:
        helper = 0
    if helper_alive(helper, root):
        if _arg(argv, "--update-helper") == str(helper):   # bizi yardımcı açtı: o bizi bekliyor
            return None
        _notify("Oskilo şu anda güncelleniyor. Birkaç saniye sonra yeniden açın.\n\n"
                "Oskilo is being updated. Please open it again in a few seconds.")
        return "busy"
    if (backup / COMMITTED).exists() and not (backup / ROLLING).exists():
        try:
            n = int(_read(backup / ATTEMPTS, "0") or 0) + 1
        except ValueError:
            n = 1
        _write(backup / ATTEMPTS, str(n))
        if n < 3:
            return None                               # bu açılış denesin; açılırsa mark_started doğrular
    # yarıda kalmış (ya da yarıda kalmış geri alma, ya da tekrar tekrar açılamayan) güncelleme: geri al
    log = _Log()
    log(f"açılışta tamamlanmamış güncelleme bulundu: {root}")
    helper_exe = root / NEW_DIR / TOP / exe_name()
    if helper_exe.is_file():
        try:
            _spawn(_cmd_for(helper_exe) + ["--rollback-update", str(root), "--wait-pid", str(os.getpid())],
                   helper_exe.parent)
            return "delegated"
        except OSError as e:
            log(f"geri alma yardımcısı başlatılamadı: {e}")
    if rollback(root, log):                           # yardımcı yoksa elden geleni yap
        _mark_failed(root / NEW_DIR, "yarıda kalan güncelleme geri alındı")
        return None
    _notify("Oskilo güncellemesi yarıda kaldı ve otomatik geri alınamadı.\n"
            "Lütfen Oskilo'yu GitHub'dan yeniden indirin.\n\n"
            "The Oskilo update could not be undone automatically.\n"
            f"Please download Oskilo again from GitHub.\n\n{REPO_URL}/releases\n\nLog: {log.path}")
    return "broken"


def mark_started(argv: list[str], root: Path | None = None) -> None:
    """Ana pencere kurulunca (main.py): yeni sürümün açıldığını bildirir."""
    root = Path(root or install_dir())
    staging, backup = root / NEW_DIR, root / OLD_DIR
    try:
        if "--updated-from" in argv:
            staging.mkdir(exist_ok=True)
            _write(staging / STARTED, __version__)
        try:
            helper = int(_read(backup / HELPER_PID, "0") or 0)
        except ValueError:
            helper = 0
        if ((backup / COMMITTED).exists() and not (backup / VERIFIED).exists()
                and not (backup / ROLLING).exists() and not (backup / ROLLEDBACK).exists()
                and not helper_alive(helper, root)):
            _write(backup / VERIFIED, "started")
    except OSError:
        pass


# ================================================================ açılış (arayüz, pencere görününce)
def _cleanup(root: Path, keep_staging: bool) -> None:
    staging, backup = root / NEW_DIR, root / OLD_DIR
    try:
        helper = int(_read(backup / HELPER_PID, "0") or 0)
    except ValueError:
        helper = 0
    if helper_alive(helper, root):
        return
    if backup.is_dir():
        if not ((backup / VERIFIED).exists() or (backup / ROLLEDBACK).exists()):
            return                              # henüz kanıtlanmadı: yedek de indirme (geri alma exe'si) kalsın
        _force_rmtree(backup)
    if not keep_staging and staging.is_dir():
        _force_rmtree(staging)
    _clean_trash(root)


def startup(argv: list[str], root: Path | None = None, current: str = __version__) -> dict:
    """Ana pencere görününce çağrılır. Güncellemenin sonucunu bildirir, artıkları temizler.

    Döndürür: {"updated_from": ver|None, "failed": log|None, "pending": {...}|None}
    """
    root = Path(root or install_dir())
    out = {"updated_from": None, "failed": None, "pending": None}
    staging = root / NEW_DIR
    if "--updated-from" in argv:
        out["updated_from"] = _arg(argv, "--updated-from", "?")
        try:
            helper = int(_arg(argv, "--update-helper", "0"))
        except ValueError:
            helper = 0

        def later():
            if helper:
                wait_pid(helper, WATCH_S + 30)
            for _ in range(20):
                _cleanup(root, keep_staging=False)
                if not staging.exists() and not (root / OLD_DIR).exists():
                    break
                time.sleep(1.5)

        threading.Thread(target=later, name="update-cleanup", daemon=True).start()
        return out
    if "--update-failed" in argv or (staging / FAILED).exists():
        out["failed"] = str(update_log_path())
    elif (staging / APPLIED).exists():
        out["updated_from"] = _read(staging / APPLIED, "?") or "?"
    pend = pending(root, current) if out["failed"] is None else None
    out["pending"] = pend
    _cleanup(root, keep_staging=pend is not None)
    return out
