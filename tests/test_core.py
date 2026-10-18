"""Çekirdek testleri:  python -m pytest tests  (ya da: python tests/test_core.py)"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from stm32logger.engine import DemoSerial, Engine  # noqa: E402
from stm32logger.exporter import export_session  # noqa: E402
from stm32logger.importer import load_file  # noqa: E402
from stm32logger.parser import parse_line  # noqa: E402
from stm32logger.session import Session  # noqa: E402


def test_parse_variants():
    assert parse_line("1450").values == [1450.0]
    assert parse_line("1, 2.5,-3,4e2").values == [1.0, 2.5, -3.0, 400.0]
    assert parse_line("1;2;3").values == [1.0, 2.0, 3.0]
    assert parse_line("1\t2").values == [1.0, 2.0]
    assert parse_line("1 2 3").values == [1.0, 2.0, 3.0]
    assert parse_line("1,2,3,").values == [1.0, 2.0, 3.0]          # sondaki virgül
    v = parse_line("5,,1200").values                                # boş alan: kanal kaymaz
    assert v[0] == 5 and v[1] != v[1] and v[2] == 1200
    v = parse_line("5,nan,1200").values
    assert len(v) == 3 and v[2] == 1200
    p = parse_line("Sicaklik:25.3 Nem=60")
    assert p.named == {"Sicaklik": 25.3, "Nem": 60.0}
    assert parse_line("START,5000").kind == "start" and parse_line("START,5000").count == 5000
    assert parse_line("END").kind == "end"
    assert parse_line("BASLA 10").count == 10
    assert parse_line("zaman,adc1,adc2").kind == "header"
    assert parse_line("ADC:1450").named == {"ADC": 1450.0}
    assert parse_line("System ready").kind == "text"                # açılış mesajı kanal adı olmaz
    assert parse_line("Boot 3").kind == "text"
    assert parse_line("# yorum").kind == "text"


def _feed(s, lines):
    for i, ln in enumerate(lines):
        s.add(float(i), parse_line(ln))
    s.finish("test")


def test_seq_gap_detection():
    s = Session(mode="dma")
    lines = ["START,2000"] + [f"{i},{i*2}" for i in range(2000) if not (1450 < i < 1780)] + ["END,2000"]
    _feed(s, lines)
    assert s.has_seq is True
    assert s.n == 2000 - 329
    assert len(s.gaps) == 1 and s.gaps[0].after_seq == 1450 and s.gaps[0].next_seq == 1780
    assert s.missing == 329
    assert s.channels == ["Kanal 1"]


def test_header_and_named():
    s = Session()
    _feed(s, ["sira,ivme,sicaklik", "0,1.5,20", "1,1.6,21", "2,1.7,22", "3,1.8,23"])
    assert s.has_seq and s.channels == ["ivme", "sicaklik"] and s.n == 4
    s2 = Session()
    _feed(s2, ["T:20 H:40", "T:21 H:41", "T:22"])
    assert s2.channels == ["T", "H"] and s2.n == 3
    assert s2.cols[1][2] != s2.cols[1][2]  # NaN


def test_engine_dma_demo_no_loss():
    with tempfile.TemporaryDirectory() as d:
        ser = DemoSerial(mode="dma", baud=2_000_000, n_samples=20000)
        ser._pending_dump = time.perf_counter()
        eng = Engine(ser, mode="dma", raw_path=os.path.join(d, "raw.txt"), idle_timeout=0.5,
                     session_factory=lambda: Session(mode="dma", sample_rate=1000))
        eng.start()
        done = None
        t_end = time.time() + 10
        while time.time() < t_end:
            try:
                done = eng.done_q.get(timeout=0.1)
                break
            except Exception:
                pass
        eng.stop()
        assert done is not None, "aktarım bitmedi"
        assert done.finish_reason == "end"
        assert done.n == 20000 and done.missing == 0 and done.expected == 20000
        assert done.channels == ["Kanal 1", "Kanal 2"]
        assert os.path.getsize(os.path.join(d, "raw.txt")) > 100000

        for fmt in ("txt", "csv", "xlsx"):
            p = export_session(done, os.path.join(d, f"out.{fmt}"), fmt, "tr")
            back = load_file(p)
            assert back.n == 20000, fmt
            assert back.channels == ["Kanal 1", "Kanal 2"], (fmt, back.channels)
            assert back.has_seq and back.missing == 0
            assert abs(back.sample_rate - 1000) < 1e-6, (fmt, back.sample_rate)


class _BurstSerial:
    """Hiç beklemeden, rastgele parçalara bölünmüş dev bir aktarım verir."""

    def __init__(self, payload: bytes):
        import random
        self._data = payload
        self._pos = 0
        self._rnd = random.Random(1)
        self.timeout = 0.05

    @property
    def in_waiting(self):
        return len(self._data) - self._pos

    def read(self, n=1):
        if self._pos >= len(self._data):
            time.sleep(self.timeout)
            return b""
        k = min(n, self._rnd.randint(1, 4096))
        out = self._data[self._pos:self._pos + k]
        self._pos += k
        return out

    def write(self, b):
        return len(b)

    def close(self):
        pass


def test_engine_burst_300k_lines_no_loss():
    n = 300_000
    payload = ("START,%d\r\n" % n + "".join(f"{i},{i % 4096},{(i * 7) % 4096}\r\n" for i in range(n))
               + "END,%d\r\n" % n).encode()
    eng = Engine(_BurstSerial(payload), mode="dma", idle_timeout=0.5,
                 session_factory=lambda: Session(mode="dma"))
    t0 = time.perf_counter()
    eng.start()
    done = eng.done_q.get(timeout=60)
    dt = time.perf_counter() - t0
    eng.stop()
    assert done.n == n and done.missing == 0 and not done.gaps
    assert done.cols[0][12345] == 12345 % 4096
    print(f"    {n} satır {dt:.2f} sn'de işlendi ({n / dt:,.0f} satır/sn, {len(payload) / dt / 1e6:.1f} MB/sn)")


def test_robust_against_corruption():
    # aşırı büyük sayı motoru çökertmemeli
    s = Session(mode="dma")
    _feed(s, ["START,6"] + [f"{i},{i}" for i in range(3)] + ["1e400,5", "99999999999999999999,1"]
          + [f"{i},{i}" for i in range(3, 6)] + ["END,6"])
    assert s.n == 6 and s.bad_lines == 2 and s.missing == 0

    # tek bozuk sıra no ("1234" yerine "34"): sahte 1200 kayıp üretmemeli
    s = Session(mode="live")
    lines = [f"{i},{i * 2}" for i in range(1220, 1234)] + ["34,2468"] + [f"{i},{i * 2}" for i in range(1235, 1250)]
    _feed(s, lines)
    assert s.missing == 0 and s.fixed_seq == 1 and s.seq[14] == 1234

    # birleşmiş iki satır (kayıp \r\n): satır reddedilir, kayıp doğru sayılır, hayalet kanal yok
    s = Session(mode="live")
    lines = [f"{i},{i},{i}" for i in range(20)] + ["20,20,2021,21,21"] + [f"{i},{i},{i}" for i in range(22, 30)]
    _feed(s, lines)
    assert len(s.channels) == 2 and s.bad_lines == 1 and s.missing == 2

    # 16 bit sayaç taşması kayıp sayılmaz
    s = Session(mode="live")
    _feed(s, [f"{i % 65536},1" for i in range(65530, 65545)])
    assert s.missing == 0 and s.out_of_order == 0 and s.n == 15


def test_connect_mid_stream():
    # bağlantı satırın ortasında açıldı: ilk satır yarım ("30,2730" yerine "1230,2730,9")
    s = Session(mode="live")
    lines = ["30,2730"] + [f"{i},{i % 97},{i % 13}" for i in range(1231, 1300) if i != 1260]
    _feed(s, lines)
    assert s.has_seq and s.channels == ["Kanal 1", "Kanal 2"]
    assert s.bad_lines == 1 and s.missing == 1 and s.gaps[0].after_seq == 1259


def test_boot_text_before_data():
    s = Session(mode="dma")
    _feed(s, ["System ready", "Boot 3", "START,4", "0,1,2", "1,1,2", "2,1,2", "3,1,2", "END,4"])
    assert s.channels == ["Kanal 1", "Kanal 2"] and s.n == 4 and s.missing == 0


def test_old_format_import():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "eski.txt")
        with open(p, "w", encoding="utf-8") as f:
            for i in range(10):
                f.write(f"[2025-06-25 16:35:{i:02d}] Sicaklik:{20+i}.5\n")
        s = load_file(p)
        assert s.n == 10 and s.channels == ["Sicaklik"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            t = time.perf_counter()
            fn()
            print(f"OK  {name}  ({time.perf_counter()-t:.2f}s)")


def test_unblock_only_known_files_and_verify():
    from stm32logger import system

    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "_internal"))
        exe = os.path.join(d, "Oskilo.exe")
        dll = os.path.join(d, "_internal", "Qt6Core.dll")
        for p in (exe, dll):
            open(p, "w").write("orijinal " + p)
        man = system.manifest_path(d)
        assert system.write_manifest(d, man) == 2
        v = system.verify_install(d, man)
        assert v["ok"] and v["checked"] == 2 and not v["extra"]

        evil = os.path.join(d, "_internal", "evil.dll")       # sonradan klasöre eklenen dosya
        open(evil, "w").write("x")
        open(dll, "a").write("değişti")                       # değiştirilmiş dosya
        for p in (exe, dll, evil, man):
            # Linux'ta ":" dosya adında geçerli; Windows'taki alternatif akışı taklit eder
            open(p + system.ZONE, "w").write("[ZoneTransfer]\nZoneId=3\n")
        assert system.is_blocked(exe)
        v = system.verify_install(d, man)
        assert not v["ok"] and v["changed"] == ["_internal/Qt6Core.dll"] and v["extra"] == ["_internal/evil.dll"]

        ok, fail, skipped = system.unblock(d, man)
        assert (ok, fail, skipped) == (2, 0, 2)                # değişmemiş exe + listenin kendisi
        assert not system.is_blocked(exe)
        assert system.is_blocked(dll) and system.is_blocked(evil)
        assert os.path.exists(exe) and os.path.exists(dll)     # asıl dosyalar duruyor
        os.remove(evil)
        os.remove(evil + system.ZONE)
        os.remove(os.path.join(d, "_internal", "Qt6Core.dll"))
        v = system.verify_install(d, man)
        assert v["missing"] == ["_internal/Qt6Core.dll"]


def test_manifest_rejects_path_tricks():
    from stm32logger import system

    with tempfile.TemporaryDirectory() as d:
        m = os.path.join(d, "m.txt")
        open(m, "w").write("a" * 64 + "  ../../etc/passwd\n" + "b" * 64 + "  ok.dll\n" + "c" * 64 + "  /abs\n")
        assert list(system.read_manifest(m)) == ["ok.dll"]


def test_safe_names_and_formula_injection():
    from stm32logger.exporter import default_filename, safe_prefix

    bad = safe_prefix('..\\..\\x/y:z*?"<>|\x01')
    assert not any(c in bad for c in '\\/:*?"<>|\x01') and bad.endswith("x_y_z_______")
    assert safe_prefix("CON") == "deney" and safe_prefix("  ") == "deney" and safe_prefix("...") == "deney"
    assert len(safe_prefix("a" * 500)) == 60
    s = Session(mode="dma")
    _feed(s, ["=cmd,+x,-y", "1,2,3", "4,5,6", "7,8,9", "10,11,12"])
    assert "/" not in default_filename(s, "a/b", "csv")
    d = tempfile.mkdtemp()
    s.channels[0] = "=HYPERLINK(\"http://x\")"
    p = export_session(s, os.path.join(d, "a.csv"), "csv")
    txt = open(p, encoding="utf-8-sig").read()
    assert "'=HYPERLINK" in txt and ",=" not in txt
    assert not [f for f in os.listdir(d) if f.endswith(".tmp")]    # geçici dosya kalmadı
    p = export_session(s, os.path.join(d, "a.xlsx"), "xlsx")
    from openpyxl import load_workbook

    wb = load_workbook(p)
    ws = wb.worksheets[1]
    heads = [c.value for c in ws[1]]
    i = heads.index("=HYPERLINK(\"http://x\")")
    assert ws.cell(1, i + 1).data_type == "s"                      # formül değil, düz metin


def test_atomic_export_keeps_old_file_on_error():
    import pytest

    from stm32logger import exporter

    d = tempfile.mkdtemp()
    p = os.path.join(d, "x.txt")
    open(p, "w").write("eski")
    s = Session()
    _feed(s, ["1", "2", "3"])
    orig = exporter._export_delim

    def boom(*a, **k):
        open(a[1], "w").write("yarım")
        raise OSError("disk full")

    exporter._export_delim = boom
    try:
        with pytest.raises(OSError):
            exporter.export_session(s, p, "txt")
    finally:
        exporter._export_delim = orig
    assert open(p).read() == "eski" and os.listdir(d) == ["x.txt"]


def test_help_guide_complete():
    from stm32logger import help as guide
    from stm32logger.i18n import STRINGS

    root = os.path.join(os.path.dirname(__file__), "..")
    for lang in ("tr", "en"):
        assert [k for k, _ in guide.topics(lang)] == guide.TOPICS
        for name in guide.image_names():
            assert os.path.exists(os.path.join(root, "assets", "help", lang, f"{name}.png")), (lang, name)
        for key in guide.TOPICS:
            html = guide.body_html(lang, key, 600)
            assert "{img:" not in html and len(html) > 200
    assert set(STRINGS["tr"]) == set(STRINGS["en"])


def test_update_version_compare():
    from stm32logger import system

    assert system._vtuple("v2.1.0") < system._vtuple("2.10.0")
    assert system._vtuple("2.1.0") == system._vtuple("v2.1.0")
    assert "issues/new" in system.issue_url("en")


# ---------------------------------------------------------------- v2.1 güvenlik / dayanıklılık
class _FakeSerial:
    """Verilen parçaları gönderir; die=True ise sonra USB çekilmiş gibi hata verir."""

    def __init__(self, chunks, die=False):
        self.chunks = list(chunks)
        self.die = die
        self.closed = False

    @property
    def in_waiting(self):
        return len(self.chunks[0]) if self.chunks else 0

    def read(self, n=1):
        if self.closed:
            raise OSError("closed")
        if self.chunks:
            return self.chunks.pop(0)
        if self.die:
            raise OSError("device removed")
        time.sleep(0.01)
        return b""

    def write(self, d):
        return len(d)

    def close(self):
        self.closed = True


def _wait(cond, timeout=10):
    t = time.time()
    while not cond() and time.time() - t < timeout:
        time.sleep(0.02)
    return cond()


def test_parser_redos_and_limits():
    from stm32logger.parser import MAX_FIELDS, MAX_LINE

    t = time.perf_counter()
    for bad in ("a" * 60000, "A" + "a." * 30000, "x" + " a" * 30000, "Z" + "a" * (MAX_LINE - 1)):
        assert parse_line(bad).kind == "text"
    assert time.perf_counter() - t < 0.5            # eski sürümde 60k harf ~50 sn donduruyordu
    v = parse_line("1,1e999,-1e999,3").values         # taşan sayı inf değil NaN olur
    assert v[0] == 1 and v[1] != v[1] and v[2] != v[2] and v[3] == 3
    assert parse_line(",".join(["1"] * (MAX_FIELDS + 5))).kind == "text"
    p = parse_line(" ".join(f"k{i}:{i}" for i in range(200)))
    assert len(p.named) == MAX_FIELDS


def test_channel_cap():
    from stm32logger.session import MAX_CHANNELS

    s = Session()
    for i in range(100):
        s.add(float(i), parse_line(f"A{i}:1 B{i}:2"))
    s.finish("t")
    assert len(s.channels) == MAX_CHANNELS and s.dropped_channels > 0
    st = s.channel_stats()
    assert st[0]["count"] == 1 and st[0]["min"] == 1.0


def test_engine_reconnect_and_lost_session():
    first = _FakeSerial([b"0,10\n1,11\n2,12\n3,13\n4,1"], die=True)
    second = _FakeSerial([b"0,20\n1,21\n2,22\n"])
    opened = []

    def reopen():
        opened.append(1)
        if len(opened) < 2:
            raise OSError("not yet")
        return second

    eng = Engine(first, mode="live", session_factory=lambda: Session(mode="live"), reopen=reopen)
    eng.start()
    events = []

    def got(kind):
        try:
            while True:
                events.append(eng.events_q.get_nowait()[0])
        except Exception:
            pass
        return kind in events

    assert _wait(lambda: got("reconnected"), 10)
    assert "lost" in events and "disconnected" not in events
    lost = eng.done_q.get(timeout=2)
    assert lost.finish_reason == "lost" and lost.n == 4 and lost.bad_lines == 1   # yarım "4,1" atıldı
    assert _wait(lambda: eng.current.n == 3)
    eng.stop()
    assert eng.ser is second


def test_engine_live_segment_and_hex_and_raw_rotation():
    d = tempfile.mkdtemp()
    raw = os.path.join(d, "ham.txt")
    data = b"".join(f"{i},{i}\n".encode() for i in range(250))
    ser = _FakeSerial([data[i:i + 100] for i in range(0, len(data), 100)])
    eng = Engine(ser, mode="live", session_factory=lambda: Session(mode="live"), raw_path=raw,
                 segment_rows=100, raw_max_bytes=600)
    eng.hex_on = True
    eng.start()
    assert _wait(lambda: eng.total_lines >= 250)
    time.sleep(0.2)
    eng.stop()
    segs = []
    while not eng.done_q.empty():
        segs.append(eng.done_q.get())
    assert [s.n for s in segs][:2] == [100, 100] and segs[0].finish_reason == "segment"
    assert sum(s.n for s in segs) == 250
    parts = sorted(f for f in os.listdir(d) if f.startswith("ham"))
    assert len(parts) >= 2
    assert b"".join(open(os.path.join(d, f), "rb").read() for f in
                    ["ham.txt"] + [f"ham_part{k}.txt" for k in range(2, len(parts) + 1)]) == data
    assert b"".join(eng.hex) == data


def test_engine_raw_write_error_reported():
    class BadFile:
        def write(self, d):
            raise OSError("disk full")

        def close(self):
            pass

    eng = Engine(_FakeSerial([]), mode="live", session_factory=Session)
    eng.raw_path = "x.txt"
    eng._raw = BadFile()
    eng._write_raw(b"abc")
    assert eng._raw is None
    assert eng.events_q.get_nowait()[1].startswith("raw:")


def test_segment_keeps_header_and_seq():
    data = b"seq,temp,hum\n" + b"".join(f"{i},{i},{i * 2}\n".encode() for i in range(25) if i != 14)
    eng = Engine(_FakeSerial([data]), mode="live", session_factory=lambda: Session(mode="live"), segment_rows=10)
    eng.start()
    assert _wait(lambda: eng.total_lines >= 25)
    time.sleep(0.2)
    eng.stop()
    segs = []
    while not eng.done_q.empty():
        segs.append(eng.done_q.get())
    assert all(s.channels == ["temp", "hum"] for s in segs)
    assert [g.missing for s in segs for g in s.gaps] == [1]       # 14 eksik: bölme sonrasında da yakalandı


def test_lost_junk_does_not_mark_next_session():
    first = _FakeSerial([b"\x00\xff"], die=True)
    second = _FakeSerial([b"START,3\n0,1\n1,2\n2,3\nEND,3\n"])
    eng = Engine(first, mode="dma", session_factory=lambda: Session(mode="dma"), reopen=lambda: second)
    eng.start()
    s = eng.done_q.get(timeout=10)
    eng.stop()
    assert s.n == 3 and s.bad_lines == 0 and s.integrity_ok


def test_theme_palettes_build():
    from stm32logger.ui import theme as th

    for mode in ("dark", "light"):
        for acc in th.ACCENTS:
            th.apply(mode, acc)
            qss = th.build_qss("assets")
            assert "{" in qss and "ACCENT" not in qss and th.ACCENT in qss
            assert th.SERIES[0] == th.ACCENT_TXT and len(set(th.SERIES)) == len(th.SERIES)
            assert th.TEXT in th.help_css()
    th.apply("dark", "neutral")


def test_theme_contrast_readable():
    """Her renk/görünümde yazılar okunur kalmalı (WCAG kontrast oranı)."""
    from stm32logger.ui import theme as th

    def lum(hx):
        c = [int(hx[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]

    def ratio(a, b):
        la, lb = sorted((lum(a), lum(b)), reverse=True)
        return (la + 0.05) / (lb + 0.05)

    for mode in ("dark", "light"):
        for acc in th.ACCENTS:
            th.apply(mode, acc)
            assert ratio(th.TEXT, th.BG) >= 7 and ratio(th.TEXT, th.BG_PANEL) >= 7, (mode, acc)
            assert ratio(th.MUTED, th.BG_PANEL) >= 4.5, (mode, acc, ratio(th.MUTED, th.BG_PANEL))
            assert ratio(th.TERM_TEXT, th.BG_PLOT) >= 4.5, (mode, acc)
            assert ratio(th.ACCENT_TXT, th.BG_PANEL) >= 3, (mode, acc, ratio(th.ACCENT_TXT, th.BG_PANEL))
    th.apply("dark", "neutral")


def test_units_and_notes_export_import():
    from stm32logger import analysis

    s = Session(mode="dma")
    _feed(s, ["seq,adc", "0,0", "1,4095", "2,2048", "3,1024"])
    s.units = {"adc": {"scale": analysis.adc_scale(3.3, 12), "offset": 0.0, "unit": "V"}}
    s.notes.append((1, 1.0, "=motor açıldı"))
    s.notes.append((99, 2.0, "son"))                 # satır sınırı dışı -> son satıra bağlanır
    d = tempfile.mkdtemp()
    p = export_session(s, os.path.join(d, "u.csv"), "csv")
    txt = open(p, encoding="utf-8-sig").read()
    assert "adc [V]" in txt and "'=motor açıldı" in txt and "3.3" in txt
    s2 = load_file(p)
    assert s2.channels == ["adc [V]"] and abs(s2.cols[0][1] - 3.3) < 1e-9
    assert [(r, txt) for r, _, txt in s2.notes] == [(1, "=motor açıldı"), (3, "son")]
    p = export_session(s, os.path.join(d, "u.xlsx"), "xlsx")
    from openpyxl import load_workbook

    ws = load_workbook(p).worksheets[1]
    heads = [c.value for c in ws[1]]
    ni = heads.index("Not") + 1
    assert ws.cell(3, ni).value == "=motor açıldı" and ws.cell(3, ni).data_type == "s"
    s3 = load_file(p)
    assert len(s3.notes) == 2 and abs(s3.cols[0][1] - 3.3) < 1e-9


def test_notes_with_delimiters_roundtrip_and_no_double_units():
    s = Session(mode="live")
    _feed(s, ["a,b", "5,10", "9,20", "2,30", "7,40"])   # a ardışık değil (sıra no sanılmasın)
    s.units = {"a": {"scale": 2.0, "offset": 0.0, "unit": ""}}      # birimsiz: sütun adı değişmez
    with s.lock:
        assert s.add_note("sıcak, fan\tdevrede\nikinci satır", 1.0, row=1) == 1
        s.add_note('tırnak "x"', 2.0)
    assert s.notes[0][2] == "sıcak, fan devrede ikinci satır"
    with s.lock:
        s.add_note("zil\x07 esc\x1b son\x7f", 3.0)
    assert s.notes[-1][2] == "zil esc son"
    assert s.unsaved()
    d = tempfile.mkdtemp()
    for fmt in ("csv", "txt"):
        info = {}
        p = export_session(s, os.path.join(d, "n." + fmt), fmt, info=info)
        assert info == {"rows": 4, "notes": 3}
        s2 = load_file(p)
        assert s2.imported and not s2.unsaved()
        assert s2.channels == ["a", "b"], (fmt, s2.channels)
        assert list(s2.cols[0]) == [10.0, 18.0, 4.0, 14.0] and list(s2.cols[1]) == [10.0, 20.0, 30.0, 40.0], fmt
        assert [(r, t) for r, _, t in s2.notes] == [(1, "sıcak, fan devrede ikinci satır"),
                                                    (3, 'tırnak "x" | zil esc son')], fmt
        # açılan dosya birimsiz yeniden kaydedilir (arayüz units={} verir): değerler ikinci kez çarpılmaz
        p2 = export_session(s2, os.path.join(d, "n2." + fmt), fmt, units={})
        assert list(load_file(p2).cols[0]) == [10.0, 18.0, 4.0, 14.0]
    p = export_session(s, os.path.join(d, "n.xlsx"), "xlsx")        # kontrol karakteri Excel'i bozmamalı
    assert len(load_file(p).notes) == 2
    s.mark_saved()
    assert not s.unsaved()
    # elle düzenlenmiş CSV: kapanmamış tırnak sonraki satırları yutmamalı
    p = os.path.join(d, "el.csv")
    with open(p, "w", encoding="utf-8") as f:
        f.write("Sıra,Not,a\n" + "".join(f'{i},{"\"oops" if i == 3 else ""},{i * 7 % 5}\n' for i in range(10)))
    s4 = load_file(p)
    assert s4.n == 10 and list(s4.cols[0])[4:6] == [3.0, 0.0]


def test_quick_cmd_sanitize_and_hex():
    from stm32logger.analysis import parse_hex, sanitize_cmds   # Qt'siz (CI'da ekran yok)

    cmds = sanitize_cmds([{"label": "a", "cmd": "X", "repeat": 1e-6}, {"label": "b", "cmd": "Y", "repeat": "nan"},
                          {"label": "c", "cmd": "Z", "repeat": 99999}, {"label": "d", "cmd": "W", "repeat": 0.5}, 5])
    assert [c["repeat"] for c in cmds] == [0.0, 0.0, 3600.0, 0.5]
    assert parse_hex("0x01, 0xA0 ff") == b"\x01\xa0\xff"
    try:
        parse_hex("zz")
        raise AssertionError
    except ValueError:
        pass


def test_analysis_fft_stats():
    import numpy as np

    from stm32logger import analysis

    fs = 1000.0
    t = np.arange(4096) / fs
    y = 2.0 * np.sin(2 * np.pi * 50 * t) + 0.5
    y[100] = np.nan
    f, a, peak, amp = analysis.spectrum(y, fs)
    assert abs(peak - 50) < 0.5 and 1.7 < amp < 2.2
    # kısa pencere (4 s): sıfır doldurma sayesinde tepe 1/T = 0,25 Hz'den daha isabetli bulunur
    ts = np.arange(4000) / fs
    _f, _a, peak2, _amp2 = analysis.spectrum(np.sin(2 * np.pi * 2.3 * ts), fs)
    assert abs(peak2 - 2.3) < 0.05
    st = analysis.stats(y)
    assert abs(st["mean"] - 0.5) < 0.01 and abs(st["rms"] - np.sqrt(2 + 0.25)) < 0.02
    assert abs(analysis.estimate_fs(t) - fs) < 1e-6
    # USB paketleri: 10'ar satır aynı zaman damgasıyla gelir -> yine ~1000 Hz bulunmalı
    burst = np.repeat(np.arange(400) * 0.01, 10)
    assert abs(analysis.estimate_fs(burst) - 1000) < 5
    # duraklama (59 s veri yok) hızı düşürmemeli
    pause = np.r_[np.arange(1000) / 1000, 60 + np.arange(1000) / 1000]
    assert abs(analysis.estimate_fs(pause) - 1000) < 5
    # periyodik sinyalde örnekleme hatası olmasın (fs/4 sinüsü: RMS 0,707)
    sq = np.tile([0.0, 1.0, 0.0, -1.0], 500_000)
    st = analysis.stats(np.r_[np.nan, sq, np.inf])
    assert abs(st["rms"] - np.sqrt(0.5)) < 1e-9 and st["min"] == -1 and st["last"] == -1
    off = analysis.stats(1e9 + np.array([0.0, 2.0, 4.0]))
    assert abs(off["std"] - np.std([0, 2, 4])) < 1e-6 and abs(off["mean"] - (1e9 + 2)) < 1e-6
    assert analysis.stats(np.array([np.inf, np.nan])) is None and analysis.stats(np.array([])) is None
    assert analysis.nearest(t, 0.0123) == 12
    assert analysis.sanitize_units({"a": {"scale": "x"}, "b": {"scale": 2, "offset": 1, "unit": "V"}}) == \
        {"b": {"scale": 2.0, "offset": 1.0, "unit": "V"}}
    assert analysis.fmt(2048.0) == "2048" and analysis.fmt(float("nan")) == "—"


def test_settings_migration_from_data_logger(tmp_path):
    """v3.0 (Oskilo): önceki 'Data Logger' ayarları ilk açılışta taşınır; sıfırlama onları geri getirmez."""
    from PySide6.QtCore import QSettings

    from stm32logger import ORG_NAME
    from stm32logger.system import app_settings, reset_settings

    QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, str(tmp_path))
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path))
    old = QSettings(ORG_NAME, "DataLogger")
    old.setValue("folder", "/x/Data Logger")
    old.setValue("accent", "pink")
    old.sync()
    s = app_settings()
    assert s.value("folder") == "/x/Data Logger" and s.value("accent") == "pink"
    s.setValue("accent", "oskilo")
    assert app_settings().value("accent") == "oskilo"        # ikinci açılışta tekrar taşınıp ezilmez
    reset_settings(s)
    assert app_settings().value("folder") is None


def test_binary_frames_decoder():
    import random
    import struct

    from stm32logger import binary as b

    frames = [b.make_frame(0x01, 0, (1, 65535)), b.make_frame(0x02, 1, (-5, 7)), b.make_frame(0x03, 2, (4000000000,)),
              b.make_frame(0x04, 3, (-2000000000, 5)), b.make_frame(0x05, 4, (1.5, float("inf"))),
              b.make_frame(b.T_START, 100), b.make_frame(b.T_END, 100)]
    stream = b"\x00junk\xa5" + frames[0] + b"\xa5\x5a\x99" + b"".join(frames[1:])
    bad = bytearray(b.make_frame(0x02, 9, (1, 2)))
    bad[9] ^= 0xFF                                            # bozuk değer -> CRC tutmaz
    stream += bytes(bad) + b.make_frame(0x02, 10, (3, 4))
    for chunked in (False, True):
        d = b.Decoder()
        out = []
        if chunked:                                          # 1–7 baytlık parçalarla (USB gibi)
            i = 0
            rnd = random.Random(1)
            while i < len(stream):
                k = rnd.randint(1, 7)
                out += d.feed(stream[i:i + k])
                i += k
        else:
            out = d.feed(stream)
        assert out[0] == ("data", 0, [1.0, 65535.0]) and out[1] == ("data", 1, [-5.0, 7.0])
        assert out[2] == ("data", 2, [4000000000.0]) and out[3] == ("data", 3, [-2000000000.0, 5.0])
        assert out[4][2][0] == 1.5 and out[4][2][1] != out[4][2][1]          # inf -> NaN
        assert out[5] == ("start", 100) and out[6] == ("end", 100)
        assert out[7] == ("crc",) and out[8] == ("data", 10, [3.0, 4.0]) and len(out) == 9, out
        assert d.crc_errors == 1 and d.skipped > 0
    assert b.crc16(b"123456789") == 0x29B1                    # CRC-16/CCITT-FALSE kontrol değeri
    assert struct.unpack("<H", b.make_frame(0x01, 0, (1,))[-2:])[0] == b.crc16(b.make_frame(0x01, 0, (1,))[2:-2])


def test_engine_binary_mode_dma_and_raw_import(tmp_path):
    from stm32logger import binary as b
    from stm32logger.importer import load_file

    chunks = [b.make_frame(b.T_START, 300)]
    for i in range(300):
        if i == 150:
            continue                                         # bir çerçeve kayıp -> eksik veri sayılmalı
        fr = bytearray(b.make_frame(0x02, i, (i, -i)))
        if i == 200:
            fr[10] ^= 1                                      # CRC hatası
        chunks.append(bytes(fr))
    chunks.append(b.make_frame(b.T_END, 300))
    data = b"".join(chunks)
    ser = _FakeSerial([data[i:i + 333] for i in range(0, len(data), 333)])
    raw = str(tmp_path / "ham.bin")
    eng = Engine(ser, mode="dma", session_factory=lambda: Session(mode="dma", seq_mode="on"),
                 raw_path=raw, idle_timeout=0.3, data_format="binary")
    eng.start()
    s = eng.done_q.get(timeout=10)
    eng.stop()
    assert s.n == 298 and s.expected == 300 and s.missing == 2 and s.crc_errors == 1, (s.n, s.missing, s.crc_errors)
    assert list(s.cols[0])[:3] == [0.0, 1.0, 2.0] and list(s.cols[1])[:3] == [0.0, -1.0, -2.0]
    assert not s.integrity_ok
    s2 = load_file(raw)
    assert s2.n == 298 and s2.crc_errors == 1 and s2.missing == 2


def test_alarm_scan_and_sanitize():
    import numpy as np

    from stm32logger import analysis as a

    y = np.array([0, 1, 5, 6, 4.9, 5.2, 2, 6, np.nan, 7, 1])
    hits, armed = a.alarm_scan(y, ">", 5.0, 1.0, True)
    assert hits == [3, 7] and armed is True        # 5.2 eşiği geçti ama 4.0'ın altına inmeden yeniden kurulmadı
    hits, armed = a.alarm_scan(y, ">", 5.0, 0.0, True)
    assert hits == [3, 5, 7] and armed is True       # NaN yok sayılır; 7 geldiğinde alarm hâlâ tetikli
    hits, armed = a.alarm_scan(np.array([6.0, 7.0]), ">", 5.0, 1.0, False)
    assert hits == [] and armed is False            # önceki parçada tetiklenmişti, hâlâ eşik üstünde
    hits, _ = a.alarm_scan(np.array([3.0, 0.5, 2.0]), "<", 1.0, 0.0, True)
    assert hits == [1]
    many = np.tile([0.0, 10.0], 5000)
    hits, armed = a.alarm_scan(many, ">", 5, 0, True, max_hits=50)
    assert len(hits) == 50 and armed is False       # son örnek eşik üstünde: alarm tetikli kalır
    assert a.sanitize_alarms([{"ch": "K1", "op": ">", "value": "3.3"}, {"ch": "", "value": 1},
                              {"ch": "K2", "value": "x"}, 5]) == \
        [{"on": True, "ch": "K1", "op": ">", "value": 3.3, "hyst": 0.0, "beep": False, "cmd": ""}]



def test_binary_flush_and_seq32():
    from stm32logger import binary as b

    d = b.Decoder()
    good = b.make_frame(0x02, 5, (1, 2))
    cut = b.make_frame(0x02, 6, (3, 4))[:-3]                  # aktarım sonunda yarım kalan çerçeve
    assert d.feed(good + cut) == [("data", 5, [1.0, 2.0])]
    assert d.flush() == [] and not d.buf and d.crc_errors == 0 and d.truncated == 1   # durdurunca yarım kalan: bozuk değil
    assert d.feed(good) == [("data", 5, [1.0, 2.0])]          # sonraki aktarım temiz başlar
    # bozuk kanal sayısı uzun bir çerçeve bekletir; flush arkadaki sağlam çerçeveleri kurtarır
    bad = bytearray(b.make_frame(0x02, 7, (1, 2)))
    bad[3] = 60
    tail = b.make_frame(0x02, 8, (5, 6)) + b.make_frame(b.T_END, 9)
    d2 = b.Decoder()
    assert d2.feed(bytes(bad) + tail) == []
    out = d2.flush()
    assert ("data", 8, [5.0, 6.0]) in out and ("end", 9) in out and ("crc",) in out
    # 32 bit sıra no: kart 40000'de yeniden başlarsa 16 bit taşma sanılmaz
    s = Session(mode="live", seq_mode="on")
    s.seq32 = True
    for i in list(range(39998, 40001)) + [0, 1, 2]:
        s.add(0.0, parse_line(f"{i},1"))
    s.flush_pending()
    assert max(s.seq) < 65536 and s.missing < 100, (s.missing, list(s.seq)[-3:])
