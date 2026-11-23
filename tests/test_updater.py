# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Mehmet Can Dusgun (mhmtcndsgn) — Oskilo
"""Uygulama içi güncelleme testleri (ağ ve Qt gerekmez).

İndirme, sahte bir GitHub sunucusu yerine `updater._open` değiştirilerek test edilir.
"""
import hashlib
import io
import json
import os
import sys
import time
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stm32logger import updater as u  # noqa: E402

os.environ["OSKILO_UPDATE_NO_UI"] = "1"           # yardımcı testlerde pencere açmasın

EXE = u.exe_name()
MAN = "_internal/manifest.sha256"
DL = u.DOWNLOAD_PREFIX


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def make_tree(root: Path, version: str, files: dict) -> Path:
    """Sahte Oskilo klasörü: dosyalar + manifest (ilk satırda sürüm)."""
    root.mkdir(parents=True, exist_ok=True)
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    rows = [f"{_sha(d)}  {rel}" for rel, d in sorted(files.items())]
    m = root / MAN
    m.parent.mkdir(parents=True, exist_ok=True)
    m.write_text(f"# Oskilo v{version} SHA-256\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return root


def snapshot(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


OLD = {EXE: b"old exe", "_internal/a.dll": b"A1", "_internal/gone.dll": b"G", "_internal/sub/x.pyd": b"X1"}
NEW = {EXE: b"new exe", "_internal/a.dll": b"A2", "_internal/added.dll": b"N", "_internal/sub/x.pyd": b"X1"}


def zip_tree(tree: Path, extra: dict | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for p in sorted(tree.rglob("*")):
            if p.is_file():
                zf.write(p, "Oskilo/" + p.relative_to(tree).as_posix())
        for name, data in (extra or {}).items():
            zf.writestr(name, data)
    return buf.getvalue()


class FakeResp(io.BytesIO):
    def __init__(self, data: bytes):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def serve(monkeypatch, files: dict):
    """updater._open'ı URL -> bayt sözlüğüyle değiştirir."""
    def fake_open(url, timeout, accept="x"):
        if url not in files:
            raise u.urllib.error.URLError("404")
        return FakeResp(files[url])
    monkeypatch.setattr(u, "_open", fake_open)


def release(version: str, zbytes: bytes, sums_digest: str | None = None) -> tuple[dict, dict]:
    zurl, surl = f"{DL}v{version}/{u.ASSET_WIN}", f"{DL}v{version}/{u.SUMS}"
    digest = sums_digest or _sha(zbytes)
    rel = {"version": version, "url": "x",
           "assets": {u.ASSET_WIN: {"url": zurl, "size": len(zbytes)}, u.SUMS: {"url": surl, "size": 100}}}
    return rel, {zurl: zbytes, surl: f"{digest}  {u.ASSET_WIN}\n{'0' * 64}  {u.ASSET_MAC}\n".encode()}


# ------------------------------------------------------------------ sürüm bilgisi
def test_parse_release_filters():
    good = {"tag_name": "v3.2.1", "html_url": "https://github.com/mhmtcndsgn/Oskilo/releases/tag/v3.2.1",
            "prerelease": True, "assets": [
                {"name": u.ASSET_WIN, "browser_download_url": f"{DL}v3.2.1/{u.ASSET_WIN}", "size": 5},
                {"name": "evil.zip", "browser_download_url": "https://evil.example/evil.zip", "size": 5}]}
    r = u.parse_release(good)
    assert r["version"] == "3.2.1" and r["prerelease"] and list(r["assets"]) == [u.ASSET_WIN]
    assert u.parse_release({**good, "draft": True}) is None
    assert u.parse_release({**good, "tag_name": "v3.2"}) is None
    assert u.parse_release({**good, "html_url": "https://evil.example"})["url"].startswith(u.REPO_URL)


def test_find_update_channels(monkeypatch):
    def rel(tag, pre):
        return {"tag_name": tag, "prerelease": pre, "html_url": f"{u.REPO_URL}/releases/tag/{tag}", "assets": []}

    data = {f"{u.API}/releases/latest": rel("v3.1.1", False),
            f"{u.API}/releases?per_page=20": [rel("v3.1.1", False), rel("v3.2.1", True), rel("v3.10.0", True),
                                             {"tag_name": "v9.9.9", "draft": True}]}
    monkeypatch.setattr(u, "_get_json", lambda url, timeout: data[url])
    r = u.find_update(beta=False, current="3.2.0")
    assert r["latest"] == "3.1.1" and not r["newer"]
    r = u.find_update(beta=True, current="3.2.0")
    assert r["latest"] == "3.10.0" and r["newer"] and r["prerelease"]      # 3.10 > 3.2 (sayısal karşılaştırma)


def test_parse_sums():
    d = "a" * 64
    s = u.parse_sums(f"{d}  {u.ASSET_WIN}\n{d.upper()} *{u.ASSET_MAC}\nbozuk satır\n")
    assert s == {u.ASSET_WIN: d, u.ASSET_MAC: d}


# ------------------------------------------------------------------ zip ve doğrulama
@pytest.mark.parametrize("bad", ["../evil.txt", "/abs.txt", "Oskilo/../../evil.txt", "Baska/x.txt", "C:/x.txt",
                                 "Oskilo\\..\\..\\evil.txt"])
def test_extract_rejects_unsafe(tmp_path, bad):
    z = tmp_path / "a.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("Oskilo/ok.txt", "ok")
        zf.writestr(bad, "x")
    with pytest.raises(u.UpdateError):
        u.extract_safe(z, tmp_path / "out")
    assert not (tmp_path / "evil.txt").exists() and not (tmp_path.parent / "evil.txt").exists()


def test_extract_rejects_symlink(tmp_path):
    z = tmp_path / "a.zip"
    with zipfile.ZipFile(z, "w") as zf:
        info = zipfile.ZipInfo("Oskilo/link")
        info.external_attr = (0o120777 << 16)
        zf.writestr(info, "/etc/passwd")
    with pytest.raises(u.UpdateError):
        u.extract_safe(z, tmp_path / "out")


def test_verify_tree(tmp_path):
    t = make_tree(tmp_path / "Oskilo", "3.2.1", NEW)
    assert len(u.verify_tree(t, "3.2.1")) == len(NEW)
    with pytest.raises(u.UpdateError):
        u.verify_tree(t, "3.2.2")                                           # beklenmeyen sürüm
    (t / "_internal/a.dll").write_bytes(b"tampered")
    with pytest.raises(u.UpdateError) as e:
        u.verify_tree(t, "3.2.1")
    assert e.value.key == "upd_e_sha"


# ------------------------------------------------------------------ indirme + hazırlık
def test_prepare_windows_ok(tmp_path, monkeypatch):
    root = make_tree(tmp_path / "app", "3.2.0", OLD)
    rel, files = release("3.2.1", zip_tree(make_tree(tmp_path / "build", "3.2.1", NEW)))
    serve(monkeypatch, files)
    seen = []
    helper = u.prepare_windows(rel, root, progress=lambda d, t, ph: seen.append(ph))
    assert helper == root / u.NEW_DIR / "Oskilo" / EXE and helper.read_bytes() == NEW[EXE]
    assert {"dl", "verify", "extract"} <= set(seen)
    assert not (root / u.NEW_DIR / u.ASSET_WIN).exists()                    # zip silindi
    p = u.pending(root, "3.2.0")
    assert p and p["version"] == "3.2.1"
    assert u.pending(root, "3.2.1") is None                                 # zaten o sürüm
    assert snapshot(root / u.NEW_DIR / "Oskilo").keys() >= set(NEW)
    assert {k: v for k, v in snapshot(root).items() if not k.startswith(u.NEW_DIR)} == snapshot(
        make_tree(tmp_path / "ref", "3.2.0", OLD))                         # kurulum henüz değişmedi


def test_prepare_windows_bad_checksum(tmp_path, monkeypatch):
    root = make_tree(tmp_path / "app", "3.2.0", OLD)
    rel, files = release("3.2.1", zip_tree(make_tree(tmp_path / "build", "3.2.1", NEW)), sums_digest="b" * 64)
    serve(monkeypatch, files)
    with pytest.raises(u.UpdateError) as e:
        u.prepare_windows(rel, root)
    assert e.value.key == "upd_e_sha" and not (root / u.NEW_DIR).exists()


def test_prepare_windows_tampered_member(tmp_path, monkeypatch):
    """Zip'in parmak izi doğru ama içindeki dosya manifest'le uyuşmuyor (ör. derleme hatası)."""
    b = make_tree(tmp_path / "build", "3.2.1", NEW)
    (b / "_internal/a.dll").write_bytes(b"changed after manifest")
    rel, files = release("3.2.1", zip_tree(b))
    serve(monkeypatch, files)
    with pytest.raises(u.UpdateError):
        u.prepare_windows(rel, make_tree(tmp_path / "app", "3.2.0", OLD))


def test_prepare_windows_cancel_and_missing_asset(tmp_path, monkeypatch):
    root = make_tree(tmp_path / "app", "3.2.0", OLD)
    rel, files = release("3.2.1", zip_tree(make_tree(tmp_path / "build", "3.2.1", NEW)))
    serve(monkeypatch, files)
    with pytest.raises(u.Cancelled):
        u.prepare_windows(rel, root, cancel=lambda: True)
    assert not (root / u.NEW_DIR).exists()
    rel["assets"].pop(u.SUMS)
    with pytest.raises(u.UpdateError) as e:
        u.prepare_windows(rel, root)
    assert e.value.key == "upd_e_asset"


def test_download_only_from_repo(tmp_path):
    with pytest.raises(u.UpdateError):
        u.download("https://evil.example/Oskilo_Windows.zip", tmp_path / "x.zip")


# ------------------------------------------------------------------ uygulama ve geri alma
def _staged(tmp_path, old=OLD, new=NEW):
    root = make_tree(tmp_path / "app", "3.2.0", old)
    (root / "kullanici_notu.txt").write_bytes(b"user file")                # kullanıcının eklediği dosya
    src = make_tree(root / u.NEW_DIR / "Oskilo", "3.2.1", new)
    (root / u.NEW_DIR / u.READY).write_text(json.dumps(
        {"version": "3.2.1", "manifest": _sha((src / MAN).read_bytes())}))
    return root, src


def _installed(root):
    return {k: v for k, v in snapshot(root).items() if not k.startswith("_oskilo_")}


def test_apply_success_and_rollback_from_journal(tmp_path):
    root, src = _staged(tmp_path)
    before = _installed(root)
    logs = []
    assert u.apply(src, root, logs.append)
    after = snapshot(root)
    for rel, data in NEW.items():
        assert after[rel] == data
    assert "_internal/gone.dll" not in after                               # yeni sürümde olmayan dosya kaldırıldı
    assert after["kullanici_notu.txt"] == b"user file"                     # kullanıcının dosyasına dokunulmadı
    assert u.manifest_version(u.manifest_of(root)) == "3.2.1"
    assert (root / u.OLD_DIR / u.COMMITTED).exists()
    assert not (root / u.OLD_DIR / "_internal/sub/x.pyd").exists()         # değişmeyen dosyaya dokunulmadı
    assert u.rollback(root, logs.append)                                  # yeni sürüm açılamadı senaryosu
    assert _installed(root) == before
    assert (root / u.OLD_DIR / u.ROLLEDBACK).exists() and not (root / u.OLD_DIR / u.COMMITTED).exists()


def test_apply_failure_midway_restores_everything(tmp_path, monkeypatch):
    root, src = _staged(tmp_path)
    before = _installed(root)
    real = u.shutil.copy2
    n = {"k": 0}

    def flaky(a, b):
        n["k"] += 1
        if n["k"] == 2:
            raise PermissionError("kilitli")                               # ör. antivirüs dosyayı tutuyor
        return real(a, b)

    monkeypatch.setattr(u.shutil, "copy2", flaky)
    monkeypatch.setattr(u, "_retry", lambda fn, *a, **k: fn(*a))           # testte bekleme olmasın
    assert not u.apply(src, root, lambda m: None)
    assert _installed(root) == before


@pytest.mark.parametrize("stop_after", range(1, 9))
def test_interrupted_apply_is_recoverable(tmp_path, monkeypatch, stop_after):
    """Süreç herhangi bir dosya işleminden sonra ölse (elektrik kesildi) bile günlükten geri alınabilmeli."""
    root, src = _staged(tmp_path)
    before = _installed(root)
    ops = {"k": 0}

    class Killed(BaseException):
        pass

    def counting(fn):
        def w(*a):
            ops["k"] += 1
            if ops["k"] > stop_after:
                raise Killed()
            return fn(*a)
        return w

    monkeypatch.setattr(u, "_retry", lambda fn, *a, **k: counting(fn)(*a))
    monkeypatch.setattr(u, "rollback", lambda root, log: False)            # ölü süreç geri alamaz
    u.apply(src, root, lambda m: None)
    monkeypatch.undo()
    if (root / u.OLD_DIR / u.COMMITTED).exists():
        return                                                             # işlem bitmişti
    # sonraki açılış: yardımcı ölü, günlük var, onaylanmamış -> geri al
    st = u.preflight([], root)
    assert st in (None, "delegated")
    if st == "delegated":                                                  # yardımcı exe ile (burada elle)
        assert u.rollback(root, lambda m: None)
    assert _installed(root) == before


@pytest.mark.skipif(sys.platform == "win32", reason="Windows'ta yardımcı, exe yolundan tanınır (gerçek exe ile packaging/test_update_windows.py dener)")
def test_preflight_busy_while_helper_runs(tmp_path):
    root, src = _staged(tmp_path)
    assert u.apply(src, root, lambda m: None)
    (root / u.OLD_DIR / u.VERIFIED).unlink(missing_ok=True)
    import subprocess
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        (root / u.OLD_DIR / u.HELPER_PID).write_text(str(p.pid), encoding="utf-8")
        assert u.preflight([], root) == "busy"                            # güncelleme sürüyor: dokunma
        assert u.preflight(["x", "--update-helper", str(p.pid)], root) is None   # yardımcının açtığı sürüm
        u.startup([], root, "3.2.1")
        assert (root / u.OLD_DIR).exists()                                 # yedek silinmedi
    finally:
        p.kill()
        p.wait()


def test_committed_but_unverified_retries_then_rolls_back(tmp_path):
    root, src = _staged(tmp_path)
    before = _installed(root)
    assert u.apply(src, root, lambda m: None)
    (root / u.OLD_DIR / u.HELPER_PID).write_text("0", encoding="utf-8")
    assert u.preflight([], root) is None and u.preflight([], root) is None   # iki deneme hakkı
    st = u.preflight([], root)                                             # 3. açılış da doğrulanmadı
    if st == "delegated":
        u.rollback(root, lambda m: None)
    assert _installed(root) == before
    # açılış başarılıysa doğrulanır ve yedek silinir
    root2, src2 = _staged(tmp_path / "b")
    assert u.apply(src2, root2, lambda m: None)
    (root2 / u.OLD_DIR / u.HELPER_PID).write_text("0", encoding="utf-8")
    assert u.preflight([], root2) is None
    u.mark_started([], root2)
    assert (root2 / u.OLD_DIR / u.VERIFIED).exists()
    u.startup([], root2, "3.2.1")
    assert not (root2 / u.OLD_DIR).exists() and not (root2 / u.NEW_DIR).exists()


def test_file_becomes_folder_and_back(tmp_path):
    old = {EXE: b"o", "_internal/foo": b"file"}
    new = {EXE: b"n", "_internal/foo/bar.dll": b"in folder"}
    root, src = _staged(tmp_path, old, new)
    before = _installed(root)
    assert u.apply(src, root, lambda m: None)
    assert (root / "_internal/foo/bar.dll").read_bytes() == b"in folder"
    assert u.rollback(root, lambda m: None)
    assert _installed(root) == before and (root / "_internal/foo").is_file()
    root2, src2 = _staged(tmp_path / "b", new, old)                        # tersi: klasör -> dosya
    assert u.apply(src2, root2, lambda m: None)
    assert (root2 / "_internal/foo").read_bytes() == b"file"


def test_stale_readonly_backup_does_not_block(tmp_path):
    root, src = _staged(tmp_path)
    stale = root / u.OLD_DIR / "x"
    stale.mkdir(parents=True)
    f = stale / "ro.dll"
    f.write_bytes(b"x")
    os.chmod(f, 0o444)
    assert u.apply(src, root, lambda m: None)
    assert (root / EXE).read_bytes() == NEW[EXE]


def test_apply_main_rejects_foreign_folders(tmp_path):
    root, _src = _staged(tmp_path)
    other = make_tree(tmp_path / "baska", "3.2.1", NEW)                    # indirme klasörü dışında
    assert u.apply_main(["x", "--apply-update", str(root), "--src", str(other), "--wait-pid", "0"]) == 2
    assert snapshot(root)[EXE] == OLD[EXE]
    assert u.apply_main(["x", "--apply-update", str(tmp_path / "yok"), "--src", str(other)]) == 2


def test_apply_main_rejects_tampered_staging(tmp_path, monkeypatch):
    root, src = _staged(tmp_path)
    (src / "_internal/a.dll").write_bytes(b"degistirildi")                 # indirildikten sonra değişti
    monkeypatch.setattr(u, "_probe", lambda root, log: True)
    assert u.apply_main(["x", "--apply-update", str(root), "--src", str(src), "--wait-pid", "0"]) == 5
    assert snapshot(root)[EXE] == OLD[EXE] and (root / u.NEW_DIR / u.FAILED).exists()
    st = u.startup([], root, "3.2.0")                                      # sonraki açılış bildirir, temizler
    assert st["failed"] and st["pending"] is None and not (root / u.NEW_DIR).exists()


def test_apply_main_no_restart(tmp_path, monkeypatch):
    root, src = _staged(tmp_path)
    monkeypatch.setattr(u, "_probe", lambda root, log: True)
    assert u.apply_main(["x", "--apply-update", str(root), "--src", str(src), "--wait-pid", "0"]) == 0
    assert (root / EXE).read_bytes() == NEW[EXE]
    assert (root / u.OLD_DIR / u.PROBED).exists() and not (root / u.OLD_DIR / u.VERIFIED).exists()
    # sonraki açılış: "güncellendi" bilgisi, artıklar temizlenir; dosya doğrulama geçici klasörleri saymaz
    from stm32logger import system
    (root / "_oskilo_trash_1").mkdir()
    v = system.verify_install(str(root), str(u.manifest_of(root)))
    assert v["ok"] and v["extra"] == ["kullanici_notu.txt"]
    assert u.preflight([], root) is None
    u.startup([], root, "3.2.1")
    assert (root / u.OLD_DIR).exists()                                     # açılış kanıtlanmadan yedek silinmez
    u.mark_started([], root)                                               # ana pencere kuruldu
    st = u.startup([], root, "3.2.1")
    assert st["updated_from"] == "3.2.0" and st["pending"] is None
    assert not (root / u.NEW_DIR).exists() and not (root / u.OLD_DIR).exists()


def test_apply_main_probe_fails_rolls_back(tmp_path, monkeypatch):
    root, src = _staged(tmp_path)
    before = _installed(root)
    monkeypatch.setattr(u, "_probe", lambda root, log: False)
    assert u.apply_main(["x", "--apply-update", str(root), "--src", str(src), "--wait-pid", "0"]) == 4
    assert _installed(root) == before
    st = u.startup([], root, "3.2.0")
    assert st["failed"] and st["pending"] is None
    assert not (root / u.NEW_DIR).exists() and not (root / u.OLD_DIR).exists()


# ------------------------------------------------------------------ açılış
def test_startup_states(tmp_path):
    root, src = _staged(tmp_path)
    st = u.startup([], root, "3.2.0")
    assert st["pending"]["version"] == "3.2.1" and st["failed"] is None
    (src / MAN).write_text((src / MAN).read_text(encoding="utf-8") + "# değişti\n", encoding="utf-8")       # bekleyen indirme bozulmuş
    assert u.pending(root, "3.2.0") is None
    root, src = _staged(tmp_path / "b")
    (root / u.NEW_DIR / u.FAILED).write_text("x", encoding="utf-8")
    st = u.startup([], root, "3.2.0")
    assert st["failed"] and st["pending"] is None and not (root / u.NEW_DIR).exists()
    # güncellemeden sonraki ilk açılış: işaret bırakır, artıkları (doğrulanınca) temizler
    (root / u.OLD_DIR).mkdir()
    (root / u.OLD_DIR / u.VERIFIED).write_text("ok", encoding="utf-8")
    (root / u.NEW_DIR).mkdir()
    u.mark_started(["x", "--updated-from", "3.2.0"], root)
    assert (root / u.NEW_DIR / u.STARTED).exists()
    st = u.startup(["x", "--updated-from", "3.2.0", "--update-helper", "0"], root, "3.2.1")
    assert st["updated_from"] == "3.2.0"
    for _ in range(50):
        if not (root / u.NEW_DIR).exists() and not (root / u.OLD_DIR).exists():
            break
        time.sleep(0.1)
    assert not (root / u.NEW_DIR).exists() and not (root / u.OLD_DIR).exists()


def test_interrupted_rollback_is_completed_next_launch(tmp_path, monkeypatch):
    """Geri alma da yarıda kalırsa: sonraki açılış karışık klasörle açılmamalı, geri almayı bitirmeli."""
    root, src = _staged(tmp_path)
    before = _installed(root)
    assert u.apply(src, root, lambda m: None)
    n = {"k": 0}
    real = os.replace

    class Killed(BaseException):
        pass

    def dying(a, b):
        n["k"] += 1
        if n["k"] > 1:
            raise Killed()
        return real(a, b)

    monkeypatch.setattr(u, "_retry", lambda fn, *a, **k: (dying if fn is os.replace else fn)(*a))
    with pytest.raises(Killed):
        u.rollback(root, lambda m: None)
    monkeypatch.undo()
    (root / u.OLD_DIR / u.HELPER_PID).write_text("0", encoding="utf-8")
    assert (root / u.OLD_DIR / u.ROLLING).exists() and not (root / u.OLD_DIR / u.COMMITTED).exists()
    u.mark_started([], root)
    assert not (root / u.OLD_DIR / u.VERIFIED).exists()                    # yarım geri alma "doğrulanmaz"
    st = u.preflight([], root)
    assert st == "delegated"
    assert u.rollback(root, lambda m: None)
    assert _installed(root) == before


def test_backup_without_journal_counts_as_rolled_back(tmp_path):
    root, _src = _staged(tmp_path)
    before = _installed(root)
    (root / u.OLD_DIR).mkdir()                                             # günlük açılmadan önce çöktü
    assert u.rollback(root, lambda m: None)
    assert (root / u.OLD_DIR / u.ROLLEDBACK).exists() and u.preflight([], root) is None
    assert _installed(root) == before


def test_exe_replaced_last_and_old_only_removed_after(tmp_path):
    old = {EXE: b"o", "_internal/python312.dll": b"py12", "_internal/a.dll": b"a1"}
    new = {EXE: b"n", "_internal/python313.dll": b"py13", "_internal/a.dll": b"a2"}
    root, src = _staged(tmp_path, old, new)
    assert u.apply(src, root, lambda m: None)
    ops = [r for r, k in u.read_journal(root / u.OLD_DIR) if k in ("replaced", "added", "removed")]
    assert ops.index(EXE) > ops.index("_internal/a.dll") and ops.index(EXE) > ops.index("_internal/python313.dll")
    assert ops.index("_internal/python312.dll") > ops.index(EXE)          # eski Python en son kalkar


def test_unreadable_journal_is_not_treated_as_empty(tmp_path, monkeypatch):
    root, src = _staged(tmp_path)
    assert u.apply(src, root, lambda m: None)
    (root / u.OLD_DIR / u.COMMITTED).unlink()                              # yarıda kalmış gibi
    real = u.Path.read_text

    def locked(self, *a, **k):
        if self.name == u.JOURNAL:
            raise PermissionError("antivirüs tarıyor")
        return real(self, *a, **k)

    monkeypatch.setattr(u.Path, "read_text", locked)
    assert not u.rollback(root, lambda m: None)
    monkeypatch.undo()
    assert not (root / u.OLD_DIR / u.ROLLEDBACK).exists()
    u.startup([], root, "3.2.1")
    assert (root / u.OLD_DIR / u.JOURNAL).exists()                         # yedek silinmedi


def test_error_before_apply_does_not_replay_previous_journal(tmp_path, monkeypatch):
    root, src = _staged(tmp_path)
    assert u.apply(src, root, lambda m: None)                              # önceki güncelleme (doğrulanmış,
    (root / u.OLD_DIR / u.VERIFIED).write_text("ok", encoding="utf-8")                       # yedeği henüz silinmemiş)
    (root / u.OLD_DIR / u.HELPER_PID).write_text("12345", encoding="utf-8")
    after = _installed(root)
    src2 = make_tree(root / u.NEW_DIR / "Oskilo", "3.2.1", NEW)
    (root / u.NEW_DIR / u.READY).write_text(json.dumps({"version": "3.2.1", "manifest": _sha((src2 / MAN).read_bytes())}))

    def boom(*a, **k):
        raise RuntimeError("beklenmeyen")

    monkeypatch.setattr(u, "other_instances", boom)
    assert u.apply_main(["x", "--apply-update", str(root), "--src", str(src2), "--wait-pid", "0"]) == 1
    assert _installed(root) == after                                       # eski günlük tekrar oynatılmadı


def test_python_core_replaced_after_other_files(tmp_path):
    old = {EXE: b"o", "_internal/base_library.zip": b"b1", "_internal/python312.dll": b"p", "_internal/z.dll": b"z1"}
    new = {EXE: b"n", "_internal/base_library.zip": b"b2", "_internal/python312.dll": b"p2", "_internal/z.dll": b"z2"}
    root, src = _staged(tmp_path, old, new)
    assert u.apply(src, root, lambda m: None)
    ops = [r for r, k in u.read_journal(root / u.OLD_DIR) if k in ("replaced", "added", "removed")]
    assert ops.index("_internal/z.dll") < ops.index("_internal/base_library.zip") < ops.index(EXE)
    assert ops.index("_internal/python312.dll") < ops.index(EXE)


def test_changed_files_uses_manifests_not_hashes(tmp_path):
    root = make_tree(tmp_path / "app", "3.2.0", OLD)
    (root / "_internal/sub/x.pyd").write_bytes(b"bozuk ama listede ayni")  # içeriğe bakılmaz
    new = u.read_manifest(str(make_tree(tmp_path / "n", "3.2.1", NEW) / MAN))
    old = u.read_manifest(str(root / MAN))
    assert u.changed_files(old, new, root) == sorted([EXE, "_internal/a.dll", "_internal/added.dll"])
    (root / "_internal/sub/x.pyd").unlink()                                # eksikse yeniden kopyalanır
    assert "_internal/sub/x.pyd" in u.changed_files(old, new, root)


def test_apply_reports_progress(tmp_path):
    root, src = _staged(tmp_path)
    seen = []
    assert u.apply(src, root, lambda m: None, progress=lambda i, n: seen.append((i, n)))
    n = seen[-1][1]
    assert seen[-1] == (n, n) and [i for i, _ in seen] == list(range(1, n + 1))
    assert n == 5                       # 3 değişen/eklenen + manifest + 1 kaldırılan


def test_progress_window_smoke(monkeypatch, tmp_path):
    """İlerleme penceresi açılıp kapanabiliyor (ekransız Qt ile)."""
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    try:
        from stm32logger.ui.update_progress import ProgressUI
    except ImportError as e:                       # ör. CI'daki Linux'ta ekran kütüphaneleri (libEGL) yok
        pytest.skip(f"Qt arayüzü yüklenemedi: {e}")

    ui = ProgressUI()
    ui.status("upd_p_copy", 0.5, i=3, n=10)
    assert "3/10" in ui.win.text and ui.win.frac == 0.5
    ui.status("upd_p_start", 0.2)
    assert ui.win.frac == 0.5                                              # geri gitmez
    ui.tick()
    ui.close()
    ui.close()


def test_corrupted_installed_file_is_renewed(tmp_path):
    """Kurulumda bozulmuş (boyutu farklı) bir dosya, listelerde "aynı" görünse de yenilenir."""
    root, src = _staged(tmp_path)
    (root / "_internal/sub/x.pyd").write_bytes(b"bozuk")                   # NEW'de de X1, boyut farklı
    old = u.read_manifest(str(root / MAN))
    new = u.read_manifest(str(src / MAN))
    assert "_internal/sub/x.pyd" in u.changed_files(old, new, root, src)
    assert u.apply(src, root, lambda m: None)
    assert (root / "_internal/sub/x.pyd").read_bytes() == b"X1"


def test_safe_ui_swallows_window_errors(tmp_path):
    class Boom:
        closed = False

        def status(self, *a, **k):
            raise RuntimeError("pencere bozuldu")

        def tick(self):
            raise RuntimeError("pencere bozuldu")

        def close(self):
            Boom.closed = True

    ui = u._SafeUI(Boom())
    ui.status("upd_p_wait", 0.1)                                           # hata yükselmez
    assert ui.inner is None and Boom.closed
    ui.tick()
    ui.close()


def test_restart_exit0_before_window_is_not_verified(tmp_path, monkeypatch):
    """Yeni sürüm ana penceresini kurmadan 0 koduyla kapanırsa (ör. "zaten açık"ta vazgeçildi)
    güncelleme doğrulanmış sayılmaz; ilk açılışlar doğrular ya da geri alır."""
    root, src = _staged(tmp_path)

    class P:
        returncode = 0

        def poll(self):
            return 0

    monkeypatch.setattr(u, "_spawn", lambda cmd, cwd: P())
    rc = u.apply_main(["x", "--apply-update", str(root), "--src", str(src), "--wait-pid", "0", "--restart"])
    assert rc == 0
    assert (root / u.OLD_DIR / u.COMMITTED).exists() and not (root / u.OLD_DIR / u.VERIFIED).exists()
