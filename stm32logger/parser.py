# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Mehmet Can Dusgun (mhmtcndsgn) — Oskilo
"""Gelen satırları çözümleyen yardımcılar.

Desteklenen satır biçimleri:
    1450                      -> tek değer
    12,345,678 / 12;34 / 12 34 / sekmeyle ayrılmış -> çok kanallı
    Sicaklik:25.3 Nem=60      -> isimli kanallar
    5,,1200 / 5,nan,1200      -> boş/geçersiz alan NaN olur, kanallar kaymaz
    zaman,adc1,adc2           -> başlık satırı (veriden önce gelirse)
    START,5000  / END,5000    -> toplu aktarım işaretleri (BASLA / BITTI da olur)
    # açıklama                -> yok sayılır
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

_NUM = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
NUM_RE = re.compile(rf"^{_NUM}$")
# (?<![\w.]) : isim sadece kelime başında başlayabilir. Bu olmadan uzun bir satırda
# her harf için yeniden deneme yapılır ve süre karesel artar (kötü niyetli/bozuk veriyle donma).
KV_RE = re.compile(rf"(?<![\w.])([A-Za-zÇĞİÖŞÜçğıöşü_][\wÇĞİÖŞÜçğıöşü.]*)\s*[:=]\s*({_NUM})")
MARKER_RE = re.compile(
    r"^\s*(START|BEGIN|BASLA|BAŞLA|END|STOP|BITTI|BİTTİ|BITIS|BİTİŞ)\b[\s,:;=]*(\d+)?",
    re.IGNORECASE,
)
HEADER_TOK_RE = re.compile(r"^[\wÇĞİÖŞÜçğıöşü().%/\-\[\] ]+$")
NAN = float("nan")
MAX_LINE = 4096        # bundan uzun satır veri sayılmaz (bozuk/birleşmiş satır, bellek ve CPU koruması)
MAX_FIELDS = 64        # bir satırdan en fazla bu kadar kanal okunur
# Eski sürümün kayıt biçimi: "[2025-06-25 16:35:00] Sicaklik:25.3"
OLD_TS_RE = re.compile(r"^\[\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?\]\s*")

_START_WORDS = {"START", "BEGIN", "BASLA", "BAŞLA"}


@dataclass
class ParsedLine:
    kind: str                      # "data" | "header" | "start" | "end" | "text" | "empty"
    values: list[float] = field(default_factory=list)   # kind == data (konumsal)
    named: dict[str, float] | None = None                # kind == data (isimli)
    names: list[str] = field(default_factory=list)       # kind == header
    count: int | None = None                             # start / end
    text: str = ""


def _is_num(tok: str) -> bool:
    return bool(NUM_RE.match(tok))


def _f(tok: str) -> float:
    """Sayıya çevirir; taşan değerler (1e999 -> inf) NaN olur, dosyaya 'inf' yazılmaz."""
    v = float(tok)
    return v if math.isfinite(v) else NAN


def parse_line(line: str) -> ParsedLine:
    s = line.strip()
    if not s:
        return ParsedLine("empty")
    if s[0] == "#" or len(s) > MAX_LINE:
        return ParsedLine("text", text=s[:200])

    # İşaret satırları: hızlı ön kontrol (rakamla başlayan satırlar işaret olamaz)
    if s[0].isalpha():
        m = MARKER_RE.match(s)
        if m and (len(s) == m.end() or not s[m.end():].strip()):
            word = m.group(1).upper()
            cnt = int(m.group(2)) if m.group(2) else None
            kind = "start" if word in _START_WORDS else "end"
            return ParsedLine(kind, count=cnt, text=s)

        kv = KV_RE.findall(s)
        if kv:
            named: dict[str, float] = {}
            for k, v in kv:
                if k in named or len(named) < MAX_FIELDS:
                    named[k] = _f(v)
            return ParsedLine("data", named=named, text=s)

    # Ayraç: virgül / noktalı virgül / tab varsa tek ayraçla böl (boş alan = NaN, kanal kaymaz)
    delim = "," if "," in s else (";" if ";" in s else ("\t" if "\t" in s else None))
    if delim:
        toks = [t.strip() for t in s.rstrip(delim + " ").split(delim)]   # "1,2,3," sondaki ayraç yok sayılır
        if len(toks) > MAX_FIELDS + 1:      # +1: sıra numarası sütunu
            return ParsedLine("text", text=s[:200])
        vals = []
        n_num = 0
        for t in toks:
            if _is_num(t):
                vals.append(_f(t))
                n_num += 1
            else:
                vals.append(NAN)
        if n_num:
            return ParsedLine("data", values=vals, text=s)
        if all(HEADER_TOK_RE.match(t) for t in toks if t) and any(toks):
            return ParsedLine("header", names=[t for t in toks], text=s)
        return ParsedLine("text", text=s)

    # Boşlukla ayrılmış: hepsi sayıysa veri, değilse metin ("Boot 3", "System ready" gibi)
    toks = s.split()
    if len(toks) <= MAX_FIELDS + 1 and all(_is_num(t) for t in toks):
        return ParsedLine("data", values=[_f(t) for t in toks], text=s)
    return ParsedLine("text", text=s)


def strip_old_timestamp(line: str) -> str:
    return OLD_TS_RE.sub("", line, count=1)


def fmt_num(v: float) -> str:
    """1450.0 -> '1450', 0.1 -> '0.1' (gereksiz sıfır yok)."""
    if v != v:  # NaN
        return ""
    if v.is_integer() and abs(v) < 1e15:
        return str(int(v))
    return format(v, ".10g")
