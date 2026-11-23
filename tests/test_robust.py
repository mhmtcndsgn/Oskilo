# SPDX-License-Identifier: GPL-3.0-or-later
"""Dayanıklılık testleri: bozuk girdi (fuzz), disk / dosya hataları, hız sınırları, uzun kayıt.

Hız sınırları bilerek geniş tutuldu (CI makinesi ve zayıf bilgisayar bunun birkaç katı yavaş olabilir);
amaç bir değişikliğin bir şeyi 10 kat yavaşlatmasını yakalamak.
"""
import errno
import gc
import os
import random
import sys
import time

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from stm32logger import analysis, binary, exporter  # noqa: E402
from stm32logger.engine import Engine  # noqa: E402
from stm32logger.exporter import export_session  # noqa: E402
from stm32logger.importer import load_file  # noqa: E402
from stm32logger.parser import parse_line  # noqa: E402
from stm32logger.session import Session  # noqa: E402

SLOW = float(os.environ.get("OSKILO_PERF_FACTOR", "1"))      # CI'da 3 gibi bir çarpan verilebilir


def _session(n, ch=4, seq=True):
    s = Session(mode="live", seq_mode="on" if seq else "off")
    with s.lock:
        for i in range(n):
            vals = ",".join(str((i * (k + 3)) % 4096) for k in range(ch))
            s.add(i * 0.001, parse_line(f"{i},{vals}" if seq else vals))
        s.flush_pending()
    return s


def _check_invariants(s: Session):
    assert all(len(c) == s.n for c in s.cols)
    assert len(s.t_host) == s.n
    if s.has_seq:
        assert len(s.seq) == s.n
    assert s.missing >= 0


# ------------------------------------------------------------------ fuzz
def test_fuzz_parser_and_session():
    rnd = random.Random(7)
    alphabet = b"0123456789,;:.-+eE \t\r\nabcXYZ#=\x00\xff\xc3\xa7STARTEND"
    for trial in range(30):
        s = Session(mode="live", seq_mode=rnd.choice(["auto", "on", "off"]))
        with s.lock:
            for _ in range(400):
                n = rnd.randint(0, 120)
                raw = bytes(rnd.choice(alphabet) for _ in range(n))
                line = raw.decode("utf-8", errors="replace")
                p = parse_line(line)
                s.add(rnd.random(), p)
            s.flush_pending()
        s.finish("stop")
        _check_invariants(s)


def test_fuzz_binary_decoder_resyncs():
    rnd = random.Random(3)
    good = [binary.make_frame(0x02, i, [i % 1000, -i % 1000]) for i in range(2000)]
    stream = bytearray()
    for i, fr in enumerate(good):
        fr = bytearray(fr)
        if i % 97 == 0:                                   # bit bozulması
            fr[rnd.randrange(len(fr))] ^= 1 << rnd.randrange(8)
        if i % 131 == 0:                                  # araya çöp
            stream += bytes(rnd.randrange(256) for _ in range(rnd.randint(1, 40)))
        stream += fr
    dec = binary.Decoder()
    frames = []
    pos = 0
    while pos < len(stream):                              # rastgele parçalar halinde
        k = rnd.randint(1, 3000)
        frames += dec.feed(bytes(stream[pos:pos + k]))
        pos += k
    frames += dec.flush()
    data = [f for f in frames if f[0] == "data"]
    assert len(data) >= 2000 - 2000 // 97 - 2000 // 131 - 5
    assert all(f[1] < 2000 for f in data)                 # bozuk çerçeve veri sayılmadı
    for _ in range(20):                                   # tamamen rastgele bayt: çökme yok, bellek sınırlı
        dec.feed(bytes(rnd.randrange(256) for _ in range(50_000)))
        assert len(dec.buf) <= binary.MAX_BUF


def test_fuzz_filters():
    rnd = np.random.default_rng(5)
    for _ in range(60):
        n = int(rnd.integers(0, 3000))
        y = rnd.normal(size=n) * 10.0 ** int(rnd.integers(-3, 9))
        if n:
            y[rnd.random(n) < rnd.random() * 0.3] = np.nan
            y[rnd.random(n) < 0.01] = np.inf
        kind = str(rnd.choice(analysis.FILTERS))
        param = float(rnd.choice([0, 1, 2, 5, 9, 50, 301, 1e6, -3, np.nan]))
        fs = float(rnd.choice([0, 1, 100, 1000, 50_000]))
        f = analysis.apply_filter(y, kind, param, fs)
        assert f.shape == y.shape
        if kind != "off" and analysis.filter_ready(kind, param, fs):
            assert np.array_equal(np.isfinite(f), np.isfinite(y))


# ------------------------------------------------------------------ disk ve dosya hataları
def test_export_disk_full_leaves_no_temp(tmp_path, monkeypatch):
    s = _session(5000)
    s.finish("stop")
    real = exporter._export_delim

    def boom(*a, **k):
        real(*a, **k)
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(exporter, "_export_delim", boom)
    with pytest.raises(OSError):
        export_session(s, str(tmp_path / "a.csv"), "csv", "tr")
    assert os.listdir(tmp_path) == []


def test_export_target_locked_or_folder(tmp_path):
    s = _session(100)
    s.finish("stop")
    target = tmp_path / "kilitli.csv"
    target.mkdir()                                       # hedef bir klasör: yazılamaz (Excel kilidi gibi)
    with pytest.raises(OSError):
        export_session(s, str(target), "csv", "tr")
    assert sorted(os.listdir(tmp_path)) == ["kilitli.csv"]


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="salt okunur klasör testi")
def test_export_read_only_folder(tmp_path):
    s = _session(100)
    s.finish("stop")
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)
    try:
        with pytest.raises(OSError):
            export_session(s, str(ro / "a.csv"), "csv", "tr")
    finally:
        ro.chmod(0o700)


def test_stale_temp_removed(tmp_path):
    old = tmp_path / ".a.csv.abc123.tmp"
    old.write_bytes(b"x" * 100)
    os.utime(old, (time.time() - 7200, time.time() - 7200))
    other = tmp_path / ".b.csv.abc.tmp"                  # başka dosyanınki: dokunulmaz
    other.write_bytes(b"x")
    os.utime(other, (time.time() - 7200, time.time() - 7200))
    s = _session(10)
    s.finish("stop")
    export_session(s, str(tmp_path / "a.csv"), "csv", "tr")
    assert not old.exists() and other.exists()


@pytest.mark.parametrize("fmt", ["csv", "txt", "xlsx"])
def test_turkish_path_roundtrip(tmp_path, fmt):
    folder = tmp_path / "Ölçüm Kayıtları ğüşiöç" / "Deney 1"
    folder.mkdir(parents=True)
    s = _session(3000)
    s.add_note("ısı ↑, şalter açık; \"tırnak\"", 0.1, row=10)
    s.finish("stop")
    p = export_session(s, str(folder / f"kayıt şçğ.{fmt}"), fmt, "tr")
    r = load_file(p)
    assert r.n == 3000 and r.notes and "şalter" in r.notes[0][2]
    assert list(r.cols[1][:5]) == list(s.cols[1][:5])


def test_formula_injection_guarded(tmp_path):
    s = Session(mode="live", seq_mode="off")
    s.header = ["=HYPERLINK(\"x\")", "+1"]
    with s.lock:
        for i in range(20):
            s.add(i, parse_line(f"{i},{i}"))
        s.flush_pending()
    s.add_note("=cmd|' /C calc'!A0", 0.0, row=1)
    s.finish("stop")
    p = export_session(s, str(tmp_path / "x.csv"), "csv", "tr")
    text = open(p, encoding="utf-8-sig").read()
    assert ",=" not in text and "\n=" not in text


# ------------------------------------------------------------------ hız sınırları
def test_perf_parse_and_session():
    lines = [f"{i},{i % 4096},{(i * 7) % 4096},{(i * 3) % 4096}" for i in range(200_000)]
    s = Session(mode="live", seq_mode="on")
    t0 = time.perf_counter()
    with s.lock:
        for i, ln in enumerate(lines):
            s.add(i * 1e-3, parse_line(ln))
        s.flush_pending()
    dt = time.perf_counter() - t0
    assert s.n == 200_000
    assert dt < 6.0 * SLOW, f"200k satır {dt:.2f} sn"          # 2 Mbaud ~ 40k satır/sn: bol pay


def test_perf_filters_plot_window():
    fs = 1000.0
    y = np.random.default_rng(1).normal(size=30_000)
    t0 = time.perf_counter()
    for kind, p in (("lowpass", 5), ("highpass", 0.5), ("notch", 50), ("mavg", 101), ("median", 5)):
        for _ in range(8):
            analysis.apply_filter(y, kind, p, fs)
    dt = time.perf_counter() - t0
    assert dt < 1.5 * SLOW, f"8 kanal × 5 filtre {dt:.2f} sn"


def test_perf_export_csv(tmp_path):
    s = _session(200_000)
    s.finish("stop")
    t0 = time.perf_counter()
    export_session(s, str(tmp_path / "a.csv"), "csv", "tr")
    dt = time.perf_counter() - t0
    assert dt < 10.0 * SLOW, f"200k satır CSV {dt:.2f} sn"


# ------------------------------------------------------------------ uzun kayıt (hızlandırılmış)
class _FastSerial:
    """Beklemeden sürekli satır üretir (saatlerce sürecek bir kaydı saniyelere sıkıştırır)."""

    def __init__(self, total):
        self.i = 0
        self.total = total
        self.timeout = 0.01

    @property
    def in_waiting(self):
        return 65536 if self.i < self.total else 0

    def read(self, n=1):
        if self.i >= self.total:
            time.sleep(0.01)
            return b""
        k = min(2000, self.total - self.i)
        out = "".join(f"{(self.i + j) % 65536},{(self.i + j) % 4096},{j % 100}\n" for j in range(k))
        self.i += k
        return out.encode()

    def write(self, data):
        return len(data)

    def close(self):
        pass


def test_soak_memory_bounded(tmp_path):
    """1.2 milyon satır, 200 bin satırda bir bölünür. Biten parçalar bırakılınca bellekten silinmeli
    (haftalarca süren kayıtta bellek büyümesin); 16 bit sıra no ~18 kez taşar ama kayıp/sıra hatası yok."""
    import weakref

    total = 1_200_000
    ser = _FastSerial(total)
    eng = Engine(ser, mode="live", session_factory=lambda: Session(mode="live", seq_mode="on"),
                 segment_rows=200_000)
    eng.start()
    refs, sizes = [], []
    t0 = time.time()
    while time.time() - t0 < 90 * SLOW and (eng.total_lines < total or len(refs) < 5):
        try:
            while True:
                s = eng.done_q.get_nowait()
                assert s.missing == 0 and s.out_of_order == 0 and s.bad_lines <= 1
                sizes.append(s.n)
                refs.append(weakref.ref(s))               # arayüz kaydedip bırakmış gibi: güçlü bağ tutma
                del s
        except Exception:
            pass
        time.sleep(0.05)
    rate = eng.total_lines / (time.time() - t0)
    eng.stop()
    cur = eng.current
    try:
        while True:
            sizes.append(eng.done_q.get_nowait().n)
    except Exception:
        pass
    assert sum(sizes) + cur.n >= total - 1, (sizes, cur.n)
    gc.collect()
    alive = [r for r in refs[:-1] if r() is not None]   # zincirde sadece son biten parça (zayıf bağ) kalabilir
    assert not alive, f"{len(alive)} eski parça bellekte kaldı"
    mem = sum(c.buffer_info()[1] * c.itemsize for c in cur.cols) + len(cur.t_host) * 8 + len(cur.seq) * 8
    assert mem < 50 * 1024 * 1024
    print(f"    {eng.total_lines:,} satır, {rate:,.0f} satır/sn")
