"""Kayıpsız seri port okuma motoru.

Eski sürümde veri kaybının sebebi, okuma thread'inin her satırda dosyayı
açıp kapatması ve arayüzü güncellemesiydi: bu sırada işletim sisteminin
seri port tamponu dolup taşıyor, aradaki baytlar kayboluyordu.

Yeni mimari üç katmanlı:

    [Okuyucu thread]  sadece ser.read() yapar, baytları kuyruğa atar.
          │           Başka hiçbir iş yapmadığı için tampon asla taşmaz.
          ▼
    [İşlemci thread]  ham yedeği yazar, satırlara böler, çözümler,
          │           oturuma ekler, aktarım bitişini algılar.
          ▼
    [Arayüz]          50 ms'de bir toplu olarak ekrana yansıtır.

Arayüz yavaşlarsa veri bellekte birikir; hiçbir zaman kaybolmaz.
"""
from __future__ import annotations

import collections
import math
import os
import queue
import random
import threading
import time
from typing import Callable

from . import binary
from .parser import ParsedLine, fmt_num, parse_line
from .session import Session

DEMO_PORT = "DEMO"
MAX_CARRY = 16384                 # satır sonu gelmeden biriken en fazla bayt (sonra satır sayılır)
TERM_LINE_MAX = 2000              # terminalde gösterilen satır uzunluğu sınırı
LIVE_SEGMENT_ROWS = 1_000_000     # canlı modda bu kadar satırda bir dosya bölünür (bellek sınırı)
RAW_MAX_BYTES = 256 * 1024 * 1024  # ham yedek bu boyuta ulaşınca yeni parçaya geçilir
HEX_CHUNK_MAX = 4096              # HEX görünüm için saklanan en büyük parça
_LOST = object()                  # okuyucudan işlemciye: "bağlantı koptu" işareti
_CRC = object()                   # ikili mod: atılan çerçeve (terminal satırı yerine sayılır)
SKIP_WARN_UNSYNCED = 512          # hiç geçerli çerçeve gelmeden bu kadar bayt atlanırsa uyar (biçim yanlış)


class Engine:
    def __init__(self, ser, *, mode: str, session_factory: Callable[[], Session],
                 raw_path: str | None = None, idle_timeout: float = 1.0,
                 reopen: Callable[[], object] | None = None, segment_rows: int = LIVE_SEGMENT_ROWS,
                 raw_max_bytes: int = RAW_MAX_BYTES, data_format: str = "text"):
        self.ser = ser
        self.data_format = data_format     # "text" (satır satır) | "binary" (Oskilo CRC çerçevesi)
        self._dec = binary.Decoder() if data_format == "binary" else None
        self._skip_reported = 0
        self._skip_t = 0.0
        self._synced = False             # bağlandıktan / koptuktan sonra ilk geçerli çerçeve geldi mi
        self.reopen = reopen              # verilirse USB koparsa yeniden bağlanmayı dener
        self.segment_rows = segment_rows
        self.raw_max_bytes = raw_max_bytes
        self.mode = mode
        self.session_factory = session_factory
        self.idle_timeout = idle_timeout
        self.raw_path = raw_path

        self.current: Session = session_factory()
        self.done_q: queue.SimpleQueue[Session] = queue.SimpleQueue()
        self.events_q: queue.SimpleQueue[tuple[str, str]] = queue.SimpleQueue()
        self.term = collections.deque(maxlen=1500)
        self.term_dropped = 0
        self.hex_on = False               # arayüz HEX görünümünü açınca True yapar
        self.hex = collections.deque(maxlen=400)
        self.reconnecting = False

        self.total_bytes = 0
        self.total_lines = 0
        self.last_rx = 0.0
        self.t0 = time.perf_counter()

        self._rx_q: queue.SimpleQueue = queue.SimpleQueue()
        self._stop = threading.Event()
        self._reader = threading.Thread(target=self._read_loop, name="serial-reader", daemon=True)
        self._proc = threading.Thread(target=self._process_loop, name="processor", daemon=True)
        self._raw = None
        self._raw_size = 0
        self._raw_part = 1

    # ----------------------------------------------------------- yaşam döngüsü
    def start(self) -> None:
        if self.raw_path:
            self._open_raw(self.raw_path)
        self._reader.start()
        self._proc.start()

    def stop(self) -> None:
        self._stop.set()
        self._reader.join(timeout=2)
        try:
            self.ser.close()
        except Exception:
            pass
        self._proc.join(timeout=30)

    def send(self, data: bytes) -> None:
        self.ser.write(data)

    @property
    def running(self) -> bool:
        return not self._stop.is_set()

    # ----------------------------------------------------------- ham yedek
    def _open_raw(self, path: str) -> None:
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            self._raw = open(path, "ab", buffering=1024 * 1024)
            self._raw_size = self._raw.tell()
        except OSError as e:
            self.events_q.put(("error", f"raw:{e}"))
            self._raw = None

    def _write_raw(self, data: bytes) -> None:
        if self._raw_size + len(data) > self.raw_max_bytes and self._raw_size > 0:
            try:
                self._raw.close()
            except OSError:
                pass
            self._raw = None
            self._raw_part += 1
            base, ext = os.path.splitext(self.raw_path)
            path = f"{base}_part{self._raw_part}{ext}"
            self._open_raw(path)
            if self._raw is None:
                return
            self.events_q.put(("raw_part", path))
        try:
            self._raw.write(data)
            self._raw_size += len(data)
        except OSError as e:   # disk doldu, USB bellek çıkarıldı...: bir kez bildir, yedeği durdur
            self.events_q.put(("error", f"raw:{e}"))
            try:
                self._raw.close()
            except OSError:
                pass
            self._raw = None

    # ----------------------------------------------------------- okuyucu
    def _read_loop(self) -> None:
        ser = self.ser
        put = self._rx_q.put
        clock = time.perf_counter
        while not self._stop.is_set():
            try:
                n = ser.in_waiting
                data = ser.read(n if n else 1)
            except Exception as e:  # USB çıkarıldı vb.
                if self._stop.is_set():
                    break
                if self.reopen is None:
                    self.events_q.put(("disconnected", str(e)))
                    self._stop.set()
                    break
                put(_LOST)
                self.events_q.put(("lost", str(e)))
                try:
                    ser.close()
                except Exception:
                    pass
                ser = self._reconnect()
                if ser is None:
                    break
                self.events_q.put(("reconnected", ""))
                continue
            if data:
                t = clock()
                self.last_rx = t
                self.total_bytes += len(data)
                put((t, data))
        put(None)  # işlemciye "bitti" sinyali

    def _reconnect(self):
        """Port geri gelene kadar saniyede bir dener. Kullanıcı bağlantıyı keserse None döner."""
        self.reconnecting = True
        try:
            while not self._stop.wait(1.0):
                try:
                    ser = self.reopen()
                except Exception:  # noqa: BLE001  port henüz yok
                    continue
                if self._stop.is_set():
                    try:
                        ser.close()
                    except Exception:
                        pass
                    return None
                self.ser = ser
                return ser
            return None
        finally:
            self.reconnecting = False

    # ----------------------------------------------------------- işlemci
    def _process_loop(self) -> None:
        try:
            self._process_loop_inner()
        except Exception as e:  # noqa: BLE001  — asla sessizce ölme
            self.events_q.put(("error", f"processor:{e!r}"))
            self._stop.set()
            try:
                with self.current.lock:
                    has_any = self.current.n
                if has_any:
                    self._finish_current("error")
            except Exception:
                pass
            self.events_q.put(("stopped", ""))
        finally:
            if self._raw:
                try:
                    self._raw.close()
                except OSError:
                    pass
                self._raw = None

    def _process_loop_inner(self) -> None:
        carry = b""
        last_flush = time.perf_counter()
        got_data_since_finish = False
        while True:
            items = []
            try:
                items.append(self._rx_q.get(timeout=0.05))
                while True:
                    items.append(self._rx_q.get_nowait())
            except queue.Empty:
                pass

            finished = False
            for it in items:
                if it is None:
                    finished = True
                    continue
                if it is _LOST:
                    # Bağlantı koptu: yarım satırı at, eldeki kaydı kapat (dosyaya yazılsın)
                    self._flush_decoder(time.perf_counter() - self.t0)   # önce: kurtarılan çerçeveler bu kayda
                    with self.current.lock:
                        has_any = self.current.n or self.current._pending
                        if carry and has_any:
                            self.current.bad_lines += 1
                    carry = b""
                    got_data_since_finish = False
                    if has_any:
                        self._finish_current("lost")
                    continue
                t, data = it
                if self._raw:
                    self._write_raw(data)
                if self.hex_on:
                    self.hex.append(data[-HEX_CHUNK_MAX:])
                got_data_since_finish = True
                if self._dec is not None:
                    self._handle_frames(t - self.t0, data)
                    continue
                buf = carry + data.replace(b"\r", b"\n")
                parts = buf.split(b"\n")
                carry = parts.pop()
                if len(carry) > MAX_CARRY:
                    parts.append(carry)
                    carry = b""
                self._handle_lines(t - self.t0, parts, len(data))

            now = time.perf_counter()
            if self._raw and now - last_flush > 0.5:
                try:
                    self._raw.flush()
                except OSError:
                    pass
                last_flush = now

            # Toplu aktarım: belirli süre veri gelmezse aktarım bitti say
            # (kuyrukta işlenmeyi bekleyen veri varsa asla bitirme: PC yavaş kalmış olabilir)
            idle_limit = self.idle_timeout
            cur = self.current
            if cur.expected is not None and cur.n < cur.expected:
                idle_limit *= 3   # START,N geldi ama satırlar eksik: biraz daha bekle
            if (self.mode == "dma" and got_data_since_finish and self.last_rx
                    and now - self.last_rx > idle_limit and self._rx_q.empty()):
                if carry:
                    self._handle_lines(self.last_rx - self.t0, [carry], 0)
                    carry = b""
                self._flush_decoder(self.last_rx - self.t0)
                got_data_since_finish = False
                with self.current.lock:
                    has_any = self.current.n or self.current._pending or self.current.saw_start
                if has_any:
                    self._finish_current("idle")

            if finished:
                if carry:
                    self._handle_lines(time.perf_counter() - self.t0, [carry], 0)
                self._flush_decoder(time.perf_counter() - self.t0)
                with self.current.lock:
                    has_any = self.current.n or self.current._pending
                if has_any:
                    self._finish_current("stop")
                self.events_q.put(("stopped", ""))
                return

    @staticmethod
    def _frames_to_items(frames) -> list:
        items = []
        for fr in frames:
            if fr[0] == "data":
                _, seq, vals = fr
                items.append((f"#{seq}: " + ", ".join(fmt_num(v) or "nan" for v in vals),
                              ParsedLine("data", values=[float(seq)] + vals)))
            elif fr[0] == "crc":
                items.append(("⚠ CRC", _CRC))
            else:
                kind, cnt = fr
                items.append((f"{kind.upper()},{cnt}", ParsedLine(kind, count=cnt)))
        return items

    def _handle_frames(self, t: float, data: bytes) -> None:
        """İkili mod: çerçeveleri çöz, her birini satır gibi işle (sıra no ilk sütun)."""
        frames = self._dec.feed(data)
        if not self._synced and any(f[0] != "crc" for f in frames):
            # ilk geçerli çerçeve: akışın ortasına bağlanınca atlanan ilk baytlar uyarı sebebi değil
            self._synced = True
            self._skip_reported = self._dec.skipped
        self._handle_items(t, self._frames_to_items(frames), len(data))
        self._report_skipped()

    def _flush_decoder(self, t: float) -> None:
        """Aktarım bitti / bağlantı gitti: yarım kalan çerçeve sonraki aktarıma taşınmasın."""
        if self._dec is None:
            return
        items = self._frames_to_items(self._dec.flush())
        if items:
            self._handle_items(t, items, 0)
        if self._synced:
            self._report_skipped(force=True)
        self._synced = False
        self._skip_reported = self._dec.skipped

    def _report_skipped(self, force: bool = False) -> None:
        """Çerçeve dışında kalan (atlanan) baytları arayüze saniyede en fazla bir kez bildirir."""
        dec = self._dec
        new = dec.skipped - self._skip_reported
        now = time.perf_counter()
        if not self._synced and new < SKIP_WARN_UNSYNCED:
            return
        if new > 0 and (force or now - self._skip_t > 1.0):
            self._skip_reported = dec.skipped
            self._skip_t = now
            self.events_q.put(("skipped", str(new)))

    def _handle_lines(self, t: float, parts: list[bytes], nbytes: int) -> None:
        items = []
        for raw in parts:
            if not raw:
                continue
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                p = parse_line(line)
            except Exception:  # noqa: BLE001
                p = None
            items.append((line, p))
        self._handle_items(t, items, nbytes)

    def _handle_items(self, t: float, items: list, nbytes: int) -> None:
        term_append = self.term.append
        s = self.current
        s.lock.acquire()
        try:
            s.bytes += nbytes
            for line, p in items:
                if len(self.term) == self.term.maxlen:
                    self.term_dropped += 1
                term_append(line if len(line) <= TERM_LINE_MAX else line[:TERM_LINE_MAX] + " …")
                if p is _CRC:                     # ikili modda atılan çerçeve: o anki kayda sayılır
                    s.crc_errors += 1
                    continue
                self.total_lines += 1
                if p is None:
                    s.bad_lines += 1
                    continue
                if p.kind == "start" and self.mode == "dma" and (s.n or s._pending):
                    # Önceki aktarım END göndermeden bitti -> onu kapat, yenisini aç
                    s.lock.release()
                    try:
                        self._finish_current("new-start")
                    finally:
                        s = self.current
                        s.lock.acquire()
                s.lines += 1
                try:
                    s.add(t, p)
                except Exception:  # noqa: BLE001  bozuk satır motoru durdurmasın
                    s.bad_lines += 1
                if (p.kind == "end" and self.mode == "dma") or (
                        self.mode == "live" and s.n >= self.segment_rows):
                    s.lock.release()
                    try:
                        self._finish_current("end" if p.kind == "end" else "segment")
                    finally:
                        s = self.current
                        s.lock.acquire()
        finally:
            s.lock.release()

    def _finish_current(self, reason: str) -> None:
        old = self.current
        with old.lock:
            old.finish(reason)
        new = self.session_factory()
        if reason in ("segment", "lost"):
            new.continue_from(old, keep_seq=reason == "segment")
        self.current = new
        self.done_q.put(old)


# ======================================================================
# DEMO: kart takılı değilken uygulamayı denemek için sahte seri port
# ======================================================================
class DemoSerial:
    """pyserial.Serial ile aynı arayüze sahip, veri üreten sahte port.

    mode="live": 50 Hz'de "sira,adc1,adc2" satırları gönderir.
    mode="dma" : bağlanınca (ve her "GONDER" komutunda) 4000 örneklik bir
                 deneyi START/END işaretleriyle, baud hızında topluca yollar.
    """

    def __init__(self, mode: str = "live", baud: int = 921600, n_samples: int = 4000, binary_frames: bool = False):
        self.mode = mode
        self.binary = binary_frames      # True: Oskilo ikili çerçeveleri gönderir (int16, 2 kanal)
        self.baud = max(1200, int(baud))
        self.n_samples = n_samples
        self.timeout = 0.05
        self.is_open = True
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._t_last = time.perf_counter()
        self._credit = 0.0
        self._seq = 0
        self._gen_t = 0.0
        self._pending_dump = time.perf_counter() + 1.0 if mode == "dma" else None
        self._run = 0

    # pyserial uyumlu arayüz ------------------------------------------------
    @property
    def in_waiting(self) -> int:
        self._produce()
        return min(len(self._buf), int(self._credit))

    def read(self, size: int = 1) -> bytes:
        deadline = time.perf_counter() + self.timeout
        while True:
            if not self.is_open:
                raise OSError("port closed")
            self._produce()
            with self._lock:
                k = min(size, len(self._buf), int(self._credit))
                if k > 0:
                    out = bytes(self._buf[:k])
                    del self._buf[:k]
                    self._credit -= k
                    return out
            if time.perf_counter() >= deadline:
                return b""
            time.sleep(0.002)

    def write(self, data: bytes) -> int:
        cmd = data.decode(errors="ignore").strip().upper()
        if self.mode == "dma" and cmd in {"GONDER", "GÖNDER", "SEND", "DUMP"}:
            self._pending_dump = time.perf_counter() + 0.2
        return len(data)

    def close(self) -> None:
        self.is_open = False

    def set_buffer_size(self, **_):
        pass

    # üretici ---------------------------------------------------------------
    def _produce(self) -> None:
        now = time.perf_counter()
        dt = now - self._t_last
        self._t_last = now
        bytes_per_s = self.baud / 10.0
        with self._lock:
            if self.mode == "live":
                self._credit = min(self._credit + dt * bytes_per_s, bytes_per_s)
                self._gen_t += dt
                while self._gen_t >= 0.02:  # 50 Hz
                    self._gen_t -= 0.02
                    t = self._seq * 0.02
                    a = 2048 + 900 * math.sin(2 * math.pi * 0.5 * t) + random.gauss(0, 25)
                    b = 1500 + 400 * math.sin(2 * math.pi * 0.13 * t + 1) + random.gauss(0, 15)
                    if self.binary:
                        self._buf += binary.make_frame(0x02, self._seq, (int(a), int(b)))
                    else:
                        self._buf += f"{self._seq},{a:.0f},{b:.0f}\r\n".encode()
                    self._seq += 1
            else:
                self._credit = min(self._credit + dt * bytes_per_s, bytes_per_s)
                if self._pending_dump and now >= self._pending_dump:
                    self._pending_dump = None
                    self._buf += self._make_dump()
                if not self._buf:
                    self._credit = 0

    def _make_dump(self) -> bytes:
        self._run += 1
        n = self.n_samples
        fs = 1000.0
        f = 2.0 + self._run * 0.5
        if self.binary:
            out = bytearray(binary.make_frame(binary.T_START, n))
            for i in range(n):
                t = i / fs
                a = 2048 + 1500 * math.exp(-t * 0.9) * math.sin(2 * math.pi * f * t) + random.gauss(0, 12)
                b = 2048 + 600 * math.exp(-t * 0.5) * math.cos(2 * math.pi * f * 0.5 * t) + random.gauss(0, 8)
                out += binary.make_frame(0x01, i, (max(0, int(a)), max(0, int(b))))
            out += binary.make_frame(binary.T_END, n)
            return bytes(out)
        lines = [f"START,{n}"]
        for i in range(n):
            t = i / fs
            # sönümlü titreşim + ikinci kanal: ivme benzeri sinyal
            a = 2048 + 1500 * math.exp(-t * 0.9) * math.sin(2 * math.pi * f * t) + random.gauss(0, 12)
            b = 2048 + 600 * math.exp(-t * 0.5) * math.cos(2 * math.pi * f * 0.5 * t) + random.gauss(0, 8)
            lines.append(f"{i},{a:.0f},{b:.0f}")
        lines.append(f"END,{n}")
        return ("\r\n".join(lines) + "\r\n").encode()


def open_port(port: str, baud: int, mode: str, binary: bool = False):
    """Gerçek ya da demo portu açar. Windows'ta sürücü tamponunu büyütür."""
    if port == DEMO_PORT:
        return DemoSerial(mode=mode, baud=baud, binary_frames=binary)
    import serial  # geç yükleme: açılışı hızlandırır

    kw = {} if os.name == "nt" else {"exclusive": True}   # Windows'ta port zaten tek kullanıcılı
    ser = serial.Serial(port, baud, timeout=0.05, write_timeout=1, **kw)
    try:
        # Windows varsayılanı ~4 KB; 1 MB'a çıkarıyoruz (taşmaya karşı ek güvence)
        ser.set_buffer_size(rx_size=1024 * 1024, tx_size=16384)
    except Exception:
        pass
    return ser


# Bilinen kart / USB-seri çip üreticileri (USB VID)
KNOWN_VIDS = {
    0x0483: "STM32",          # STMicroelectronics (ST-Link VCP, USB CDC)
    0x2341: "Arduino",        # Arduino SA
    0x2A03: "Arduino",        # Arduino.org
    0x303A: "ESP32",          # Espressif (yerleşik USB)
    0x1A86: "CH340",          # WCH (Arduino klonları, ESP kartları)
    0x10C4: "CP210x",         # Silicon Labs (ESP32 DevKit vb.)
    0x0403: "FTDI",           # FTDI
    0x2E8A: "Pico",           # Raspberry Pi Pico
    0x239A: "Adafruit",
    0x16C0: "Teensy",
}


def list_ports() -> list[tuple[str, str, bool]]:
    """(port, açıklama, bilinen_kart_mı) listesi. Tanınan kartlar öne alınır,
    Bluetooth sanal portları sona atılır."""
    from serial.tools import list_ports as lp

    out = []
    for p in lp.comports():
        desc = p.description or p.device
        board = KNOWN_VIDS.get(p.vid or -1)
        up = desc.upper()
        if not board and ("ST-LINK" in up or "STM" in up):
            board = "STM32"
        if board and board.upper() not in up:
            desc = f"{desc} [{board}]"
        is_bt = "BLUETOOTH" in up
        out.append((p.device, desc, bool(board), is_bt))
    out.sort(key=lambda x: (not x[2], x[3], x[0]))
    return [(d, desc, known) for d, desc, known, _ in out]
