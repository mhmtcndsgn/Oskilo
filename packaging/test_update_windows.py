# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Mehmet Can Dusgun (mhmtcndsgn) — Oskilo
"""Uygulama içi güncellemeyi gerçek Windows paketiyle dener (CI'da, derlemeden sonra).

    python packaging/test_update_windows.py dist/Oskilo

Derlenen klasörden bir "eski kurulum" (bazı dosyaları ve exe'si farklı, bir dosyası sadece eskide
olan, sürümü 3.0.0) ve onun içinde bir "indirilmiş yeni sürüm" (9.9.9) hazırlanır. Sonra yeni
sürümün exe'si gerçekten yardımcı olarak çalıştırılır:

A) kapanışta güncelleme + güncellenen uygulamanın kendi testi (--selftest) + artıkların temizlenmesi
B) yeniden başlatarak güncelleme (ilerleme penceresi açık): yeni sürüm açılır, açılış işareti gelir,
   artıklar temizlenir; deneme açılışı yapılmaz (gerçek açılış aynı kontrolü yapar)
C) başka bir Oskilo penceresi açıkken güncelleme ertelenmeli, hiçbir dosya değişmemeli
   (C2: aynısı SUBST ile eşlenmiş bir sürücü harfi üzerinden)
D) yarıda kalmış güncelleme: Oskilo açılınca geri alma, indirme klasöründeki exe'ye devredilir;
   az önce çalışmış olan Oskilo.exe dahil her dosya eski haline döner
E) yeni sürüm açılamıyor (bozuk modül): deneme açılışı başarısız, her şey geri alınır
Klasör adında Türkçe karakter ve boşluk vardır. Mesaj kutuları kapalıdır (OSKILO_UPDATE_NO_UI=1).
"""
import hashlib
import json
import os
import shutil
import string
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stm32logger import system, updater as u  # noqa: E402

os.environ["OSKILO_UPDATE_NO_UI"] = "1"
DIST = Path(sys.argv[1]).resolve()
BASE = Path(os.environ.get("RUNNER_TEMP") or os.environ.get("TEMP") or ".") / "Masaüstü deneme"
NEW_VER = "9.9.9"
EXE = u.exe_name()
WIN = sys.platform == "win32"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def write_manifest(root: Path, version: str) -> None:
    man = root / "_internal" / "manifest.sha256"
    rows = []
    for p in sorted(root.rglob("*")):
        if p.is_file() and p != man and not p.relative_to(root).parts[0].startswith("_oskilo_"):
            rows.append(f"{sha(p)}  {p.relative_to(root).as_posix()}")
    man.write_text(f"# Oskilo v{version} SHA-256\n" + "\n".join(rows) + "\n", encoding="utf-8", newline="\n")


def stamp_ready(root: Path, src: Path) -> None:
    man = src / "_internal" / "manifest.sha256"
    (root / u.NEW_DIR / u.READY).write_text(json.dumps({"version": NEW_VER, "manifest": sha(man)}), encoding="utf-8")


def prepare(name: str, base: Path = BASE, old_exe_differs: bool = True) -> tuple[Path, Path]:
    root = base / name / "Oskilo"
    shutil.rmtree(root.parent, ignore_errors=True)
    shutil.copytree(DIST, root)
    # eski kurulum: yardım görselleri ve exe farklı + sadece eskide olan bir dosya
    for p in sorted((root / "_internal" / "assets" / "help").rglob("*.png"))[:6]:
        p.write_bytes(p.read_bytes() + b"eski")
    if old_exe_differs:                       # önyükleyici sondaki fazladan baytları yok sayar
        with open(root / EXE, "ab") as f:
            f.write(b"\0" * 64)
    (root / "_internal" / "eski_surum_dosyasi.dat").write_bytes(b"sadece eski surumde")
    write_manifest(root, "3.0.0")
    (root / "kullanici_kaydi.csv").write_text("kullanıcı verisi", encoding="utf-8")
    # indirilmiş yeni sürüm (doğrulanmış gibi): derlemenin kendisi, sürümü 9.9.9
    src = root / u.NEW_DIR / "Oskilo"
    shutil.copytree(DIST, src)
    man = src / "_internal" / "manifest.sha256"
    lines = man.read_text(encoding="utf-8").splitlines()
    man.write_text(f"# Oskilo v{NEW_VER} SHA-256\n" + "\n".join(lines[1:]) + "\n", encoding="utf-8", newline="\n")
    stamp_ready(root, src)
    return root, src


def installed(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): sha(p) for p in root.rglob("*")
            if p.is_file() and not p.relative_to(root).parts[0].startswith("_oskilo_")
            and "__pycache__" not in p.parts}


def helper(root: Path, src: Path, pid: int, restart: bool, ui: bool = False) -> int:
    cmd = [str(src / EXE), "--apply-update", str(root), "--src", str(src), "--wait-pid", str(pid)]
    if restart:
        cmd.append("--restart")
    env = dict(os.environ)
    if ui:                                                  # ilerleme penceresi de açılsın
        env.pop("OSKILO_UPDATE_NO_UI", None)
    t0 = time.monotonic()
    rc = subprocess.run(cmd, timeout=400, env=env).returncode
    print(f"  yardımcı {time.monotonic() - t0:.1f} sn sürdü (kod {rc})")
    return rc


def log_tail(n: int = 10) -> str:
    p = u.update_log_path()
    return "\n".join(p.read_text(encoding="utf-8").splitlines()[-n:]) if p.exists() else "(kayıt yok)"


def check_new(root: Path) -> None:
    r = system.verify_install(str(root), str(root / "_internal" / "manifest.sha256"))
    assert r["ok"], r
    assert sorted(r["extra"]) == ["kullanici_kaydi.csv"], r["extra"]
    assert u.manifest_version(u.manifest_of(root)) == NEW_VER
    assert sha(root / EXE) == sha(DIST / EXE), "exe değişmedi"
    assert not (root / "_internal" / "eski_surum_dosyasi.dat").exists()
    assert (root / "kullanici_kaydi.csv").read_text(encoding="utf-8") == "kullanıcı verisi"


def running_from(root: Path) -> list[int]:
    if WIN:
        return u.other_instances(root, set())
    out = subprocess.run(["pgrep", "-f", str(root / EXE)], capture_output=True, text=True).stdout
    return [int(x) for x in out.split()]


def kill_all() -> None:
    if WIN:
        subprocess.run(["taskkill", "/F", "/IM", EXE], capture_output=True)
    else:
        subprocess.run(["pkill", "-9", "-f", str(BASE)], capture_output=True)
    time.sleep(1.5)


def start_app(exe: Path, *args: str) -> subprocess.Popen:
    kw = {"creationflags": 0x08000000} if WIN else {}       # CREATE_NO_WINDOW
    return subprocess.Popen([str(exe), *args], **kw)


def wait_until(cond, timeout: float, step: float = 0.5) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(step)
    return cond()


# ---------------------------------------------------------------------------------- senaryolar
def scenario_a() -> None:
    root, src = prepare("A")
    old = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(4)"])   # "kapanmakta olan" eski sürüm
    threading.Thread(target=old.wait, daemon=True).start()
    t0 = time.monotonic()
    rc = helper(root, src, old.pid, restart=False)
    print(log_tail())
    assert rc == 0, rc
    assert time.monotonic() - t0 >= 3.5, "eski sürümün kapanması beklenmedi"
    check_new(root)
    assert (root / u.OLD_DIR / u.PROBED).exists()
    (root / "kullanici_kaydi.csv").unlink()               # selftest bütünlükte "ek dosya" saymasın
    out = root.parent / "selftest.txt"
    p = subprocess.run([str(root / EXE), "--selftest", str(out)], timeout=180)
    print(out.read_text(encoding="utf-8") if out.exists() else "(selftest çıktısı yok)")
    assert p.returncode == 0 and "integrity=ok" in out.read_text(encoding="utf-8")
    assert (root / u.OLD_DIR / u.VERIFIED).exists() or not (root / u.OLD_DIR).exists()
    if (root / u.OLD_DIR).exists() or (root / u.NEW_DIR).exists():   # selftest temizlikten önce bittiyse
        st = u.startup([], root, NEW_VER)
        assert st["updated_from"] == "3.0.0", st
    assert not (root / u.OLD_DIR).exists() and not (root / u.NEW_DIR).exists(), os.listdir(root)
    print("A OK: kapanışta güncelleme")


def scenario_b() -> None:
    root, src = prepare("B")
    rc = helper(root, src, 0, restart=True, ui=True)
    print(log_tail())
    assert rc == 0, rc
    check_new(root)
    assert running_from(root), "yeni sürüm açık değil"
    log = u.update_log_path().read_text(encoding="utf-8")
    assert "deneme açılışı" not in log.split("===== güncelleme")[-1], "yeniden başlatmada deneme açılışı olmamalı"
    assert "ilerleme penceresi açılamadı" not in log.split("===== güncelleme")[-1], "ilerleme penceresi açılamadı"
    ok = wait_until(lambda: not (root / u.OLD_DIR).exists() and not (root / u.NEW_DIR).exists(), 30)
    left = [d for d in (u.OLD_DIR, u.NEW_DIR) if (root / d).exists()]
    kill_all()
    assert ok, left
    print("B OK: yeniden başlatarak güncelleme")


def scenario_c(base: Path = BASE, name: str = "C") -> None:
    if not WIN:                                            # diğer pencereleri bulma Windows'a özgü
        return
    root, src = prepare(name, base, old_exe_differs=False)
    before = installed(root)
    other = start_app(root / EXE, "--no-first-run")
    assert wait_until(lambda: running_from(root) == [other.pid], 20), running_from(root)
    rc = helper(root, src, 0, restart=True)                # eski sürüm "ertelendi" bilgisiyle yeniden açılır
    print(log_tail(4))
    kill_all()
    assert rc == 6, rc
    assert installed(root) == before, "dosyalar değişti"
    assert u.pending(root, "3.0.0") is not None, "bekleyen güncelleme kaybolmamalı"
    print(f"{name} OK: başka pencere açıkken ertelendi ({root})")


def scenario_c2() -> None:
    if not WIN:
        return
    real = BASE / "C2_hedef"
    shutil.rmtree(real, ignore_errors=True)
    real.mkdir(parents=True)
    letter = next((f"{c}:" for c in reversed(string.ascii_uppercase[7:]) if not os.path.exists(f"{c}:\\")), None)
    if letter is None or subprocess.run(["subst", letter, str(real)], capture_output=True).returncode != 0:
        print("C2 atlandı (subst kullanılamadı)")
        return
    try:
        scenario_c(Path(letter + "\\"), "C2")
    finally:
        subprocess.run(["subst", letter, "/D"], capture_output=True)


def scenario_d() -> None:
    root, src = prepare("D")
    before = installed(root)
    rc = helper(root, src, 0, restart=False)
    assert rc == 0, rc
    check_new(root)
    # işlem "commit"ten önce kesilmiş gibi: işaretler yok, yardımcı ölü
    for m in (u.COMMITTED, u.PROBED, u.VERIFIED):
        (root / u.OLD_DIR / m).unlink(missing_ok=True)
    (root / u.OLD_DIR / u.HELPER_PID).write_text("0")
    p = start_app(root / EXE, "--no-first-run")          # yeni exe açılır, yarım işi görür, geri almayı devreder
    rc = p.wait(60)
    assert rc == 0, rc
    ok = wait_until(lambda: (root / u.OLD_DIR / u.ROLLEDBACK).exists(), 120)
    print(log_tail(8))
    assert ok, "geri alma tamamlanmadı"
    assert wait_until(lambda: bool(running_from(root)), 30), "eski sürüm yeniden açılmadı"
    kill_all()
    assert installed(root) == before, "geri alınan kurulum eskisiyle aynı değil (exe dahil)"
    st = u.startup([], root, "3.0.0")
    assert st["failed"] and st["pending"] is None, st
    assert not (root / u.OLD_DIR).exists() and not (root / u.NEW_DIR).exists()
    print("D OK: yarıda kalan güncelleme açılışta geri alındı")


def scenario_e() -> None:
    root, src = prepare("E")
    before = installed(root)
    bad = next(iter(sorted((src / "_internal" / "numpy").rglob("_multiarray_umath*"))))
    bad.write_bytes(b"bozuk modul")                       # yeni sürüm açılamaz
    write_manifest(src, NEW_VER)
    stamp_ready(root, src)
    rc = helper(root, src, 0, restart=False)
    print(log_tail(6))
    assert rc == 4, rc
    assert installed(root) == before, "deneme başarısızken kurulum değişti"
    st = u.startup([], root, "3.0.0")
    assert st["failed"], st
    assert not (root / u.OLD_DIR).exists() and not (root / u.NEW_DIR).exists()
    print("E OK: açılamayan sürüm geri alındı")


if __name__ == "__main__":
    try:
        for fn in (scenario_a, scenario_b, scenario_c, scenario_c2, scenario_d, scenario_e):
            kill_all()
            fn()
    finally:
        kill_all()
    print("GÜNCELLEME TESTİ OK")
