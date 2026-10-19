"""Bir kayıt oturumunun (bir deney / bir aktarım) verilerini tutar.

Veriler kanal başına `array('d')` dizilerinde tutulur; bu hem bellekte
kompakt hem de grafiğe hızlı aktarılır. Ekleme işlemcisi (processor) ayrı
bir thread'de çalışır; arayüz kilit altında kopya alır.

Sıra numarası (ilk sütun 0,1,2,...) varsa her satır kontrol edilir:
  * ileri atlama  -> kayıp (Gap)
  * tek bir bozuk sayı (ör. "1234" yerine "34") -> kayıp sayılmaz, düzeltilir
  * 16 bit sayaç taşması (65535 -> 0) -> devam sayılır
"""
from __future__ import annotations

import datetime as _dt
import math
import threading
from array import array
from bisect import bisect_left
from dataclasses import dataclass

from .parser import ParsedLine

NAN = float("nan")
FAST_SEQ_ROWS = 3      # ilk 3 satır ardışıksa hemen karar ver
SLOW_SEQ_ROWS = 5      # değilse 5 satıra bak (bağlanınca yarım gelen ilk satır vb.)
LOCK_COLS_AFTER = 10   # bu kadar düzgün satırdan sonra sütun sayısı sabitlenir
MAX_CHANNELS = 64      # bundan fazla kanal açılmaz (bozuk "isim:değer" akışı belleği şişirmesin)
_INT_LIMIT = 9.0e18


@dataclass
class Gap:
    after_seq: int
    next_seq: int

    @property
    def missing(self) -> int:
        return self.next_seq - self.after_seq - 1


def _as_int(v: float) -> int | None:
    if v != v or not math.isfinite(v) or abs(v) > _INT_LIMIT or not float(v).is_integer():
        return None
    return int(v)


_CTRL = {c: " " for c in (*range(0x00, 0x20), 0x7F)}


def clean_note(text) -> str:
    """Not metni tek satır ve kontrol karaktersiz olsun (CSV/TXT satır yapısı bozulmasın,
    Excel dosyası yazılabilsin), en fazla 500 karakter."""
    return " ".join(str(text).translate(_CTRL).split())[:500]


def _dedupe(names: list[str]) -> list[str]:
    out, seen = [], {}
    for n in names:
        n = n or "?"
        if n in seen:
            seen[n] += 1
            n = f"{n}_{seen[n]}"
        else:
            seen[n] = 1
        out.append(n)
    return out


class Session:
    def __init__(self, *, mode: str = "live", seq_mode: str = "auto",
                 channel_prefix: str = "Kanal", port: str = "", baud: int = 0,
                 sample_rate: float = 0.0):
        self.mode = mode                  # "live" | "dma" | "file"
        self.seq_mode = seq_mode          # "auto" | "on" | "off"
        self.channel_prefix = channel_prefix
        self.port = port
        self.baud = baud
        self.sample_rate = sample_rate
        self.started_at = _dt.datetime.now()   # ilk satır gelince güncellenir
        self.ended_at: _dt.datetime | None = None
        self.lock = threading.Lock()
        self._touched = False

        self.channels: list[str] = []
        self.cols: list[array] = []
        self.t_host = array("d")          # PC'ye ulaşma anı (sn)
        self.seq = array("q")             # sıra numarası (varsa)
        self.n = 0

        self.header: list[str] | None = None
        self.has_seq: bool | None = {"on": True, "off": False}.get(seq_mode)
        self._pending: list[tuple[float, ParsedLine]] = []
        self._ncols: int | None = None    # sabitlenmiş alan sayısı
        self._ncols_seen = 0
        self._last_seq: int | None = None
        self._seq_off = 0                 # 16 bit taşma düzeltmesi
        self._tent: tuple[int, int, int] | None = None   # (son_iyi, şüpheli, seq indeksi)

        self.gaps: list[Gap] = []
        self.out_of_order = 0             # geri giden sıra numarası
        self.fixed_seq = 0                # bozuk gelip düzeltilen sıra numarası
        self.bad_lines = 0                # bozuk satır (yanlış sütun sayısı vb.)
        self.crc_errors = 0               # ikili modda CRC'si tutmayan (atılan) çerçeve
        self.seq32 = False                # sıra no 32 bit (ikili çerçeve): 16 bit taşma düzeltmesi yapılmaz
        self.dropped_channels = 0         # MAX_CHANNELS aşıldığı için alınmayan kanal değeri
        self.expected: int | None = None  # START,N
        self.end_count: int | None = None  # END,N
        self.saw_start = False
        self.saw_end = False
        self.text_lines = 0               # sayısal olmayan satırlar
        self.bytes = 0
        self.lines = 0
        self.finish_reason = ""
        self.saved_path: str | None = None   # başarıyla kaydedildiği dosya (arayüz doldurur)
        self.saved_n = 0                     # o kayıtta dosyaya yazılan satır sayısı
        self.exporting = 0                   # sürmekte olan kayıt işi sayısı
        self.autosave_path: str | None = None  # bu kaydın güncellendiği dosya (hep aynı dosya)
        self.autosave_fmt = ""
        self.autosave_user = False            # autosave_path'i kullanıcı "Farklı kaydet" ile seçti
        self.resave_pending: tuple[str, str] | None = None  # (mod, biçim): yazma sürerken yeni kayıt istendi
        self.save_failed = False              # son otomatik kayıt başarısız: dakikada bir yeniden denenir
        self.notes: list[tuple[int, float, str]] = []   # (satır, PC zamanı, not) — kayıt sırasında eklenen notlar
        self.units: dict = {}                 # dışa aktarımda kullanılan birim dönüşümleri (arayüz verir)
        self.saved_notes = 0                  # dosyaya yazılan not sayısı
        self.imported = False                 # dosyadan açıldı (değerler zaten dönüştürülmüş olabilir)
        self.ephemeral = False                # tetikli kayıt modunda sürekli akış: sadece ekran/arabellek,
                                              # kendisi kaydedilmez (tetiklenen parçalar ayrı oturum olur)
        self.name_tag = ""                    # dosya adına eklenen etiket (ör. "tetik")
        # tetik arabelleği zinciri (motor bölünce/koparsa doldurur): sonraki oturum (güçlü) ve önceki (zayıf)
        self.next_session: Session | None = None
        self.prev_ref = None

    def add_note(self, text: str, t: float, row: int | None = None) -> int:
        """Not ekler; notun bağlandığı satırı döndürür (verilmezse o anki son satır)."""
        row = max(0, self.n - 1) if row is None else max(0, min(int(row), max(0, self.n - 1)))
        self.notes.append((row, t, clean_note(text)))
        return row

    def unsaved(self) -> bool:
        """Dosyaya yazılmamış satır ya da not var mı? (Tetik arabelleği hiçbir zaman 'kaydedilmemiş' sayılmaz.)"""
        if self.ephemeral:
            return False
        return self.n > self.saved_n or len(self.notes) > self.saved_notes

    def mark_saved(self) -> None:
        self.saved_n = self.n
        self.saved_notes = len(self.notes)

    def continue_from(self, old: "Session", keep_seq: bool) -> None:
        """Bölünen (segment) ya da kopup yeniden bağlanan kayıtta kanal adları/sütunlar aynı kalsın.

        keep_seq=True: sıra numarası kaldığı yerden devam eder (bölme sınırındaki kayıp da yakalanır).
        Bağlantı koptuysa kart yeniden başlamış olabilir; o durumda sıra kontrolü sıfırdan başlar.
        """
        self.header = list(old.header) if old.header else None
        self.seq32 = old.seq32
        self.ephemeral = old.ephemeral
        if self.seq_mode == "auto" and old.has_seq is not None:
            self.has_seq = old.has_seq
        self._ncols = old._ncols
        self._ncols_seen, self._ncols_last = old._ncols_seen, old._ncols_last
        for name in old.channels:
            self._ensure(name)
        if keep_seq:
            self._last_seq = old._last_seq
            self._seq_off = old._seq_off

    # ------------------------------------------------------------------ ekleme
    def add(self, t: float, p: ParsedLine) -> None:
        """İşlemci thread'i çağırır (kilit dışarıda alınır)."""
        k = p.kind
        if k == "empty":
            return
        if not self._touched:
            self._touched = True
            self.started_at = _dt.datetime.now()
        if k == "data":
            if self.has_seq is None:
                self._pending.append((t, p))
                self._decide_seq()
                return
            self._add_data(t, p)
        elif k == "header":
            if self.n == 0 and not self._pending:
                self.header = _dedupe(p.names)
            else:
                self.text_lines += 1
        elif k == "start":
            self.saw_start = True
            self.expected = p.count
            self._resolve_tentative()
            self._last_seq = None
            self._seq_off = 0
        elif k == "end":
            self.saw_end = True
            self.end_count = p.count
            if self.expected is None and p.count is not None:
                self.expected = p.count
        elif k == "text":
            self.text_lines += 1

    def flush_pending(self) -> None:
        if self._pending:
            if self.has_seq is None:
                self.has_seq = False
            pend, self._pending = self._pending, []
            for t, p in pend:
                self._add_data(t, p)

    def _decide_seq(self) -> None:
        pend = self._pending
        first = pend[0][1]
        if first.named is not None or len(first.values) < 2:
            self.has_seq = False
            self.flush_pending()
            return
        heads = [(_as_int(p.values[0]) if p.values else None) for _, p in pend]

        def consecutive(hs):
            return all(h is not None for h in hs) and all(hs[i + 1] - hs[i] == 1 for i in range(len(hs) - 1))

        if len(pend) >= FAST_SEQ_ROWS and consecutive(heads):
            self.has_seq = True
        elif len(pend) >= SLOW_SEQ_ROWS:
            rest = heads[1:]
            ok_steps = sum(1 for i in range(len(rest) - 1)
                           if rest[i] is not None and rest[i + 1] is not None and rest[i + 1] - rest[i] == 1)
            self.has_seq = all(h is not None for h in rest) and ok_steps >= len(rest) - 2
            # Bağlantı yayının ortasında açıldıysa ilk satır yarım gelmiş olabilir
            if not self.saw_start:
                v0, v1 = pend[0][1].values, pend[1][1].values
                bad0 = len(v0) < len(v1) or (self.has_seq and (heads[0] is None or heads[0] != heads[1] - 1))
                if bad0:
                    self.bad_lines += 1
                    pend.pop(0)
        else:
            return
        self.flush_pending()

    def _ensure(self, name: str) -> int:
        try:
            return self.channels.index(name)
        except ValueError:
            if len(self.channels) >= MAX_CHANNELS:
                self.dropped_channels += 1
                return -1
            self.channels.append(name)
            col = array("d", [NAN]) * self.n if self.n else array("d")
            self.cols.append(col)
            return len(self.channels) - 1

    def _default_name(self, i: int) -> str:
        return f"{self.channel_prefix} {i + 1}"

    def _add_data(self, t: float, p: ParsedLine) -> None:
        row: dict[int, float] = {}
        seq_val = None
        if p.named is not None:
            for name, v in p.named.items():
                i = self._ensure(name)
                if i >= 0:
                    row[i] = v
        else:
            vals = p.values
            # Sütun sayısı sabitlendikten sonra farklı uzunluktaki satır = bozuk (birleşmiş/yarım satır)
            if self._ncols is not None and len(vals) != self._ncols:
                self.bad_lines += 1
                return
            hdr = self.header
            if self.has_seq and vals:
                raw = _as_int(vals[0])
                if raw is None:
                    self.bad_lines += 1
                    return
                seq_val = self._check_seq(raw)
                if len(vals) > 1:
                    vals = vals[1:]
                    if hdr and len(hdr) == len(vals) + 1:
                        hdr = hdr[1:]
            if hdr and len(hdr) != len(vals):
                hdr = None
                if self.n == 0:
                    self.header = None
            for i, v in enumerate(vals):
                if i < len(self.channels):
                    idx = i
                else:
                    idx = self._ensure(hdr[i] if hdr else self._default_name(i))
                    if idx < 0:
                        break
                row[idx] = v
            if self._ncols is None:
                n_fields = len(p.values)
                if self._ncols_seen and n_fields == self._ncols_last:
                    self._ncols_seen += 1
                else:
                    self._ncols_seen, self._ncols_last = 1, n_fields
                if self._ncols_seen >= LOCK_COLS_AFTER:
                    self._ncols = n_fields
        if not row:
            return
        for i, col in enumerate(self.cols):
            col.append(row.get(i, NAN))
        if self.has_seq:
            self.seq.append(seq_val if seq_val is not None else self.n)
        self.t_host.append(t)
        self.n += 1

    _ncols_last = 0

    def _check_seq(self, raw: int) -> int:
        """Sıra numarasını kontrol eder; saklanacak (taşma düzeltilmiş) değeri döndürür."""
        s = raw + self._seq_off
        last = self._last_seq
        if (not self.seq32 and last is not None and s < last and last - s > 32768
                and last - self._seq_off <= 65535 and raw < 32768):
            self._seq_off += 65536            # 16 bit sayaç 65535 -> 0
            s = raw + self._seq_off

        if self._tent is not None:
            good, sus, idx = self._tent
            self._tent = None
            if s == good + 2 and sus != good + 1:
                # şüpheli değer aslında good+1'di, sadece bozuk geldi -> düzelt
                self.seq[idx] = good + 1
                self.fixed_seq += 1
                self._last_seq = s
                return s
            self._commit(good, sus)
            last = self._last_seq = sus

        if last is None or s == last + 1:
            self._last_seq = s
        else:
            # beklenmeyen değer: bir sonraki satıra bakıp karar vereceğiz
            self._tent = (last, s, len(self.seq))
            self._last_seq = s
        return s

    def _commit(self, good: int, val: int) -> None:
        if val > good + 1:
            self.gaps.append(Gap(good, val))
        elif val <= good:
            self.out_of_order += 1

    def _resolve_tentative(self) -> None:
        if self._tent is not None:
            good, sus, _ = self._tent
            self._tent = None
            self._commit(good, sus)

    # ------------------------------------------------------------- sorgular
    @property
    def missing(self) -> int:
        gap_missing = sum(g.missing for g in self.gaps)
        exp_missing = 0
        if self.expected is not None and self.ended_at is not None:
            exp_missing = max(0, self.expected - self.n)
        return max(gap_missing, exp_missing)

    @property
    def integrity_known(self) -> bool:
        return bool(self.has_seq) or self.expected is not None

    @property
    def integrity_ok(self) -> bool:
        return self.missing == 0 and self.out_of_order == 0 and self.bad_lines == 0 and self.crc_errors == 0

    def finish(self, reason: str) -> None:
        self.flush_pending()
        self._resolve_tentative()
        self.ended_at = _dt.datetime.now()
        self.finish_reason = reason

    def x_values(self, start: int = 0, end: int | None = None, prefer: str = "auto") -> tuple[list, str]:
        """Dışa aktarım için x ekseni: ("time"|"sample"|"host")."""
        end = self.n if end is None else end
        kind = prefer
        if kind == "auto":
            kind = "host" if self.mode == "live" else ("time" if self.sample_rate > 0 else "sample")
        if kind == "time" and self.sample_rate > 0:
            fs = self.sample_rate
            if self.has_seq and len(self.seq) >= end and end > 0:
                s0 = self.seq[0]
                return [(s - s0) / fs for s in self.seq[start:end]], "time"
            return [i / fs for i in range(start, end)], "time"
        if kind == "host":
            return list(self.t_host[start:end]), "host"
        if self.has_seq and len(self.seq) >= end and end > 0:
            return list(self.seq[start:end]), "sample"
        return list(range(start, end)), "sample"

    def index_from_host_time(self, t0: float) -> int:
        return bisect_left(self.t_host, t0)

    def channel_stats(self, n: int | None = None) -> list[dict]:
        import numpy as np

        n = self.n if n is None else n
        out = []
        for name, col in zip(self.channels, self.cols):
            a = np.frombuffer(col[:n], dtype=np.float64) if n else np.empty(0)
            a = a[np.isfinite(a)]
            cnt = int(a.size)
            if not cnt:
                out.append({"name": name, "count": 0})
                continue
            mean = float(a.mean())
            out.append({"name": name, "count": cnt, "min": float(a.min()), "max": float(a.max()),
                        "mean": mean, "std": float(a.std()) if cnt > 1 else 0.0})
        return out

    def duration_s(self) -> float:
        end = self.ended_at or _dt.datetime.now()
        return (end - self.started_at).total_seconds()


def extract(parts, reason: str = "trigger") -> "Session":
    """Bir ya da birkaç ardışık oturum parçasından yeni bir oturum üretir (tetikli kayıt).

    parts: [(oturum, başlangıç_satırı, bitiş_satırı), ...] — aynı bağlantının art arda gelen
    oturumları (canlı kayıt bölünmüş olabilir). Kanallar adlarına göre hizalanır, eksik kanal NaN olur.
    Notlar ve (sıra no varsa) aradaki kayıplar da taşınır; PC zamanları değişmez.
    """
    base = parts[0][0]
    out = Session(mode=base.mode, seq_mode=base.seq_mode, channel_prefix=base.channel_prefix,
                  port=base.port, baud=base.baud, sample_rate=base.sample_rate)
    out.seq32 = base.seq32
    out.header = list(base.header) if base.header else None
    names: list[str] = []
    for s, _a, _b in parts:
        for nm in s.channels:
            if nm not in names:
                names.append(nm)
    out.channels = names
    out.cols = [array("d") for _ in names]
    has_seq = True
    first_t = last_t = None
    prev_last_seq = None
    for s, a, b in parts:
        with s.lock:
            b = max(0, min(int(b), s.n))
            a = max(0, min(int(a), b))
            k = b - a
            if not k:
                continue
            for j, nm in enumerate(names):
                if nm in s.channels:
                    out.cols[j].extend(s.cols[s.channels.index(nm)][a:b])
                else:
                    out.cols[j].extend(array("d", [NAN]) * k)
            th = s.t_host[a:b] if len(s.t_host) >= b else array("d", [NAN]) * k
            out.t_host.extend(th)
            if first_t is None:
                first_t = (s, th[0])
            last_t = (s, th[-1])
            seq_ok = bool(s.has_seq) and len(s.seq) >= b
            if seq_ok:
                out.seq.extend(s.seq[a:b])
                lo, hi = s.seq[a], s.seq[b - 1]
                if prev_last_seq is not None and lo > prev_last_seq + 1:
                    out.gaps.append(Gap(prev_last_seq, lo))
                out.gaps.extend(Gap(g.after_seq, g.next_seq) for g in s.gaps if g.after_seq >= lo and g.next_seq <= hi)
                prev_last_seq = hi
            else:
                has_seq = False
            for row, t, txt in s.notes:
                if a <= row < b:
                    out.notes.append((out.n + row - a, t, txt))
            out.n += k
    out.has_seq = has_seq and out.n > 0
    if not out.has_seq:
        out.seq = array("q")
        out.gaps = []
    out._touched = True

    def _at(ref):
        s, t = ref
        try:
            t0 = s.t_host[0]
            return s.started_at + _dt.timedelta(seconds=float(t - t0)) if math.isfinite(t - t0) else s.started_at
        except (IndexError, OverflowError, ValueError):
            return s.started_at

    if first_t is not None:
        out.started_at = _at(first_t)
        out.ended_at = _at(last_t)
    else:
        out.ended_at = out.started_at
    out.finish_reason = reason
    out.notes.sort(key=lambda x: x[0])
    return out
