"""Oskilo ikili veri çerçevesi (CRC16 korumalı).

Metin satırları yerine ikili göndermek hem daha hızlıdır (2–4 bayt/değer) hem de her çerçevenin
CRC'si kontrol edildiği için bozuk veri asla kayda girmez. Bütün sayılar küçük-endian (STM32,
ESP32, AVR'nin kendi bayt sırası).

Çerçeve:

    A5 5A | TİP (1) | KANAL SAYISI (1) | SIRA NO (uint32) | DEĞERLER (kanal × boyut) | CRC16 (2)

    TİP   0x01 uint16   0x02 int16   0x03 uint32   0x04 int32   0x05 float32
          0xF0 START (SIRA NO = beklenen örnek sayısı, kanal sayısı 0)
          0xF1 END   (SIRA NO = gönderilen örnek sayısı, kanal sayısı 0)
    CRC16 CRC-16/CCITT-FALSE (poly 0x1021, başlangıç 0xFFFF), TİP'ten değerlerin sonuna kadar.

Örnek gönderme kodu: firmware_ornek/oskilo_ikili.h
"""
from __future__ import annotations

import binascii
import math
import struct

SYNC = b"\xa5\x5a"
MAX_CH = 64
TYPES = {0x01: ("H", 2), 0x02: ("h", 2), 0x03: ("I", 4), 0x04: ("i", 4), 0x05: ("f", 4)}
T_START, T_END = 0xF0, 0xF1
TYPE_NAMES = {0x01: "uint16", 0x02: "int16", 0x03: "uint32", 0x04: "int32", 0x05: "float32"}
_HDR = struct.Struct("<BBI")
MAX_BUF = 1 << 20       # bu kadar çözülemeyen bayt birikirse eskisi atılır (bellek koruması)


def crc16(data: bytes) -> int:
    """CRC-16/CCITT-FALSE."""
    return binascii.crc_hqx(data, 0xFFFF)


def make_frame(type_code: int, seq: int, values=()) -> bytes:
    """Çerçeve üretir (testler ve DEMO portu için; kart tarafındaki C koduyla aynı)."""
    if type_code in (T_START, T_END):
        body = _HDR.pack(type_code, 0, seq & 0xFFFFFFFF)
    else:
        fmt, _ = TYPES[type_code]
        body = _HDR.pack(type_code, len(values), seq & 0xFFFFFFFF) + struct.pack(f"<{len(values)}{fmt}", *values)
    return SYNC + body + struct.pack("<H", crc16(body))


class Decoder:
    """Bayt akışından çerçeveleri ayıklar. Parça parça gelen veriyi birleştirir; bozuk çerçeveyi
    (CRC tutmuyor) ve aradaki çöp baytları atlayıp bir sonraki çerçeveye senkronlanır."""

    def __init__(self):
        self.buf = bytearray()
        self.crc_errors = 0
        self.skipped = 0            # çerçeve dışında kalan (atlanan) bayt
        self.truncated = 0          # akış bittiğinde yarım kalan son çerçeve (bozuk sayılmaz)
        self._structs: dict[tuple[int, int], struct.Struct] = {}

    def reset(self) -> None:
        self.buf.clear()

    def _payload(self, tcode: int, nch: int) -> struct.Struct:
        st = self._structs.get((tcode, nch))
        if st is None:
            fmt, _ = TYPES[tcode]
            st = self._structs[(tcode, nch)] = struct.Struct(f"<{nch}{fmt}")
        return st

    def feed(self, data: bytes) -> list[tuple]:
        """Döndürür (geliş sırasıyla): [("data", sıra, [değerler]) | ("start", n) | ("end", n) | ("crc",), ...]
        ("crc",) = CRC'si tutmayan ya da yarım kalan (atılan) çerçeve."""
        buf = self.buf
        buf += data
        out = []
        pos = 0
        n = len(buf)
        while True:
            i = buf.find(SYNC, pos)
            if i < 0:
                keep = 1 if n > pos and buf[-1] == 0xA5 else 0    # yarım gelmiş senkron baytı
                self.skipped += max(0, n - pos - keep)
                pos = n - keep
                break
            self.skipped += i - pos
            if n - i < 8:                                        # başlık henüz tamam değil
                pos = i
                break
            tcode, nch = buf[i + 2], buf[i + 3]
            if tcode in (T_START, T_END):
                size = 0 if nch == 0 else -1
            elif tcode in TYPES and 1 <= nch <= MAX_CH:
                size = TYPES[tcode][1] * nch
            else:
                size = -1
            if size < 0:                                         # geçersiz başlık: sahte senkron
                self.skipped += 1
                pos = i + 1
                continue
            end = i + 8 + size + 2
            if end > n:                                          # çerçevenin kalanı bekleniyor
                pos = i
                break
            body = bytes(buf[i + 2:i + 8 + size])
            if crc16(body) != buf[end - 2] | (buf[end - 1] << 8):
                self.crc_errors += 1
                self.skipped += 1
                out.append(("crc",))
                pos = i + 1                                      # bir bayt kaydır, yeniden senkronlan
                continue
            seq = _HDR.unpack_from(body, 0)[2]
            if tcode == T_START:
                out.append(("start", seq))
            elif tcode == T_END:
                out.append(("end", seq))
            else:
                vals = [float(v) for v in self._payload(tcode, nch).unpack_from(body, 6)]
                if tcode == 0x05:
                    vals = [v if math.isfinite(v) else math.nan for v in vals]
                out.append(("data", seq, vals))
            pos = end
        if pos:
            del buf[:pos]
        if len(buf) > MAX_BUF:
            self.skipped += len(buf) - MAX_BUF
            del buf[:len(buf) - MAX_BUF]
        return out

    def flush(self) -> list[tuple]:
        """Akış bitti (aktarım sonu, bağlantı kesildi): bekleyen yarım çerçeve artık tamamlanmayacak.
        Bozuk sayılır; arkasında kalan tam çerçeveler yine de çıkarılır. Tampon boşalır, böylece
        sonraki aktarıma eski bayt taşınmaz."""
        out = self.feed(b"")
        while True:
            i = self.buf.find(SYNC)
            if i < 0:
                break
            self.skipped += i + 1
            del self.buf[:i + 1]
            rec = self.feed(b"")
            if any(f[0] != "crc" for f in rec):
                # arkasında sağlam çerçeve var: takılan başlık bozukmuş (ör. kanal sayısı baytı bozuldu)
                self.crc_errors += 1
                out.append(("crc",))
            else:
                self.truncated += 1               # sadece akışın sonunda yarım kalmış: normal, bozuk sayılmaz
            out += rec
        self.skipped += len(self.buf)
        self.buf.clear()
        return out
