"""Analiz yardımcıları (arayüzden bağımsız, test edilebilir).

* Birim dönüşümü: gerçek değer = ham × çarpan + ofset   (ör. ADC → Volt)
* FFT: Hann pencereli genlik spektrumu ve tepe frekansı
* İstatistik: son / min / max / ortalama / RMS / std
"""
from __future__ import annotations

import math

import numpy as np

FFT_MAX_POINTS = 262_144      # FFT'ye en fazla bu kadar son örnek girer (hız için)


# ------------------------------------------------------------------ birimler
def unit_of(units: dict | None, name: str) -> dict | None:
    """Kanalın dönüşüm ayarı; varsayılan (×1 +0, birimsiz) ise None."""
    if not units:
        return None
    u = units.get(name)
    if not u:
        return None
    scale = float(u.get("scale", 1.0))
    offset = float(u.get("offset", 0.0))
    unit = str(u.get("unit", "")).strip()
    if scale == 1.0 and offset == 0.0 and not unit:
        return None
    return {"scale": scale, "offset": offset, "unit": unit}


def convert(values, u: dict | None):
    """numpy dizisi ya da tek sayı."""
    if u is None:
        return values
    return values * u["scale"] + u["offset"]


def label(name: str, u: dict | None) -> str:
    return f"{name} [{u['unit']}]" if u and u.get("unit") else name


def sanitize_units(raw) -> dict:
    """Ayarlardan okunan birim sözlüğünü doğrular (bozuk değerler atılır)."""
    out = {}
    if not isinstance(raw, dict):
        return out
    for name, u in list(raw.items())[:256]:
        try:
            scale = float(u.get("scale", 1.0))
            offset = float(u.get("offset", 0.0))
            unit = str(u.get("unit", ""))[:16]
        except (TypeError, ValueError, AttributeError):
            continue
        if math.isfinite(scale) and math.isfinite(offset):
            out[str(name)[:80]] = {"scale": scale, "offset": offset, "unit": unit}
    return out


def adc_scale(vref: float, bits: int) -> float:
    """ADC sayısını volta çeviren çarpan: Vref / (2^bit − 1)."""
    return vref / (2 ** int(bits) - 1)


# ------------------------------------------------------------------ örnekleme hızı
def estimate_fs(t: np.ndarray) -> float:
    """PC zaman damgalarından (s) örnekleme hızı; tahmin edilemezse 0.

    Satırlar USB'den paketler halinde gelir (aynı paketteki satırlar aynı zamanı alır), bu yüzden
    farkların medyanı kullanılmaz: örnek sayısı / geçen süre. Duraklamalar (kart veri göndermeyi
    bir süre kesti) hesaba katılmaz: tipik paket aralığının 10 katından uzun boşluklar atlanır.
    """
    if t is None or len(t) < 3:
        return 0.0
    d = np.diff(np.asarray(t, dtype=np.float64))
    d = d[np.isfinite(d) & (d >= 0)]
    pos = d[d > 0]
    if not len(pos):
        return 0.0
    keep = d[d <= 10 * float(np.median(pos))]
    span = float(keep.sum())
    return len(keep) / span if span > 0 else 0.0


# ------------------------------------------------------------------ FFT
def spectrum(y: np.ndarray, fs: float):
    """Genlik spektrumu. Döndürür: (frekanslar, genlikler, tepe_frekansı, tepe_genliği) ya da None.

    Ortalama (DC) çıkarılır, Hann penceresi uygulanır; genlik, aynı genlikte bir sinüsün
    tepe değerini verecek şekilde ölçeklenir. NaN değerler ortalamayla doldurulur.
    """
    y = np.asarray(y, dtype=np.float64)
    if len(y) > FFT_MAX_POINTS:
        y = y[-FFT_MAX_POINTS:]
    finite = np.isfinite(y)
    if finite.sum() < 8:
        return None
    mean = float(y[finite].mean())
    y = np.where(finite, y, mean) - mean
    n = len(y)
    w = np.hanning(n)
    # Sıfır doldurma: kısa pencerelerde eğri yumuşar, tepe frekansı daha isabetli bulunur
    # (gerçek çözünürlük değişmez, ara noktalar enterpolasyondur).
    nfft = 1 << max(12, int(np.ceil(np.log2(n))) + 2)
    nfft = max(n, min(nfft, 1 << 18))
    amp = np.abs(np.fft.rfft(y * w, n=nfft)) * 2.0 / w.sum()
    freqs = np.fft.rfftfreq(nfft, d=1.0 / (fs if fs > 0 else 1.0))
    if len(amp) < 3:
        return None
    k = int(np.argmax(amp[1:])) + 1
    return freqs[1:], amp[1:], float(freqs[k]), float(amp[k])


def useful_fmax(freqs: np.ndarray, amps: np.ndarray, share: float = 0.995) -> float:
    """Gücün %99,5'inin bulunduğu frekansa kadar olan aralık (FFT grafiğinde ilk görünüm için)."""
    if not len(freqs):
        return 0.0
    p = np.asarray(amps, dtype=np.float64) ** 2
    tot = p.sum()
    if tot <= 0:
        return float(freqs[-1])
    k = int(np.searchsorted(np.cumsum(p), share * tot))
    k = min(len(freqs) - 1, max(k, 1))
    return float(min(freqs[-1], max(freqs[k] * 1.5, freqs[min(len(freqs) - 1, 8)])))


# ------------------------------------------------------------------ istatistik
def stats(y: np.ndarray) -> dict | None:
    """Son / min / max / ortalama / RMS / std (NaN ve sonsuzlar atlanır)."""
    y = np.asarray(y, dtype=np.float64)
    mask = np.isfinite(y)
    cnt = int(np.count_nonzero(mask))
    if not cnt:
        return None
    f = y if cnt == len(y) else y[mask]
    last = float(f[-1])
    d = f - f[0]                 # kaydırılmış toplamlar: büyük ofsetli veride de std kararlı
    s1 = float(d.sum()) / cnt
    var = max(0.0, float(np.dot(d, d)) / cnt - s1 * s1)
    return {"n": cnt, "last": last, "min": float(f.min()), "max": float(f.max()),
            "mean": float(f[0]) + s1, "rms": math.sqrt(float(np.dot(f, f)) / cnt), "std": math.sqrt(var)}


def nearest(x: np.ndarray, xv: float) -> int:
    """Sıralı x dizisinde xv'ye en yakın indeks."""
    if not len(x):
        return -1
    i = int(np.searchsorted(x, xv))
    if i <= 0:
        return 0
    if i >= len(x):
        return len(x) - 1
    return i if abs(x[i] - xv) < abs(x[i - 1] - xv) else i - 1


def fmt(v: float) -> str:
    """Okunaklı sayı: 0.0001234 → 1.234e-04, 2048.0 → 2048, 3.14159 → 3.142"""
    if v != v or v is None:
        return "—"
    a = abs(v)
    if a != 0 and (a < 1e-3 or a >= 1e7):
        return f"{v:.3e}"
    if float(v).is_integer():
        return str(int(v))
    return f"{v:.4g}" if a < 1 else f"{v:.6g}"


# ------------------------------------------------------------------ hızlı komutlar
def parse_hex(text: str) -> bytes:
    """"01 A0 FF", "0x01,0xA0" gibi yazımları bayta çevirir (geçersizse ValueError)."""
    return bytes.fromhex(text.replace("0x", "").replace("0X", "").replace(",", " "))


def _repeat(v) -> float:
    """Tekrar aralığı: 0 (tekrar yok) ya da 0,1–3600 s (bozuk ayar portu boğmasın)."""
    r = float(v or 0)
    if not math.isfinite(r) or r < 0.1:
        return 0.0
    return round(min(3600.0, r), 1)


def sanitize_cmds(raw) -> list[dict]:
    out = []
    if not isinstance(raw, list):
        return out
    for c in raw[:12]:
        try:
            out.append({"label": str(c.get("label", "?"))[:24], "cmd": str(c.get("cmd", ""))[:500],
                        "hex": bool(c.get("hex", False)), "eol": str(c.get("eol", "\r\n"))[:2],
                        "repeat": _repeat(c.get("repeat", 0))})
        except (TypeError, ValueError, AttributeError):
            continue
    return out


# ------------------------------------------------------------------ eşik alarmları
MAX_ALARMS = 16
TRIG_PRE_MAX = 60          # tetikli kayıt: tetikten önce en fazla bu kadar saniye kayda girer
TRIG_DUR_MIN, TRIG_DUR_MAX = 1, 3600


def sanitize_alarms(raw) -> list[dict]:
    """Ayarlardan okunan alarm listesini doğrular."""
    out = []
    if not isinstance(raw, list):
        return out
    for a in raw[:MAX_ALARMS]:
        try:
            thr = float(a.get("value"))
            hyst = abs(float(a.get("hyst", 0) or 0))
            op = ">" if str(a.get("op", ">")) == ">" else "<"
            ch = str(a.get("ch", "")).strip()[:80]
        except (TypeError, ValueError, AttributeError):
            continue
        if not ch or not math.isfinite(thr) or not math.isfinite(hyst):
            continue
        out.append({"on": bool(a.get("on", True)), "ch": ch, "op": op, "value": thr, "hyst": hyst,
                    "beep": bool(a.get("beep", False)), "cmd": str(a.get("cmd", "") or "")[:200],
                    "rec": bool(a.get("rec", False))})
    return out


def alarm_scan(y, op: str, thr: float, hyst: float, armed: bool, max_hits: int = 200):
    """Yeni gelen örneklerde eşik geçişlerini bulur.

    Alarm 'kurulu' iken koşul (y > eşik ya da y < eşik) ilk sağlandığı örnekte tetiklenir; sonra
    değer eşiğin 'geri dönüş payı' kadar gerisine inmeden yeniden kurulmaz (eşik etrafında
    titreyen sinyal alarm yağdırmasın). NaN değerler yok sayılır.
    Döndürür: (tetiklenen örneklerin indisleri, son kurulu durumu)
    """
    y = np.asarray(y, dtype=np.float64)
    n = len(y)
    if not n:
        return [], armed
    with np.errstate(invalid="ignore"):
        if op == ">":
            hit, back = y > thr, y < thr - hyst
        else:
            hit, back = y < thr, y > thr + hyst
        if hyst == 0:                                  # pay yoksa eşiğin öbür yanı yeter
            back = ~hit & ~np.isnan(y) & (y != thr)
    hits = []
    i = 0
    while i < n and len(hits) < max_hits:
        idx = np.flatnonzero(hit[i:] if armed else back[i:])
        if not len(idx):
            break
        j = i + int(idx[0])
        if armed:
            hits.append(j)
        armed = not armed
        i = j + 1
    if i < n and len(hits) >= max_hits:               # çok sık tetikleniyor: son durumu hesapla
        last_hit = np.flatnonzero(hit[i:])
        last_back = np.flatnonzero(back[i:])
        lh = last_hit[-1] if len(last_hit) else -1
        lb = last_back[-1] if len(last_back) else -1
        if lh >= 0 or lb >= 0:
            armed = bool(lb > lh)
    return hits, armed
