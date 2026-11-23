# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Mehmet Can Dusgun (mhmtcndsgn) — Oskilo
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


# ================================================================== FİLTRELER
# Grafikte gürültüyü süzmek için. Hepsi "sıfır faz"lıdır (sinyal sağa/sola kaymaz) ve sadece
# görüntüyü / isteğe bağlı ek sütunları etkiler; kaydedilen ham veri hiç değişmez.
FILTERS = ("off", "mavg", "median", "lowpass", "highpass", "notch")
FILTER_DEFAULTS = {"mavg": 9.0, "median": 5.0, "lowpass": 10.0, "highpass": 0.5, "notch": 50.0}
MAX_TAPS = 65537                 # FFT ile süzüldüğü için uzun katsayı dizisi de hızlıdır
MEDIAN_MAX = 101                 # medyan penceresi (daha büyüğü zayıf bilgisayarda grafiği yavaşlatır)


def filter_needs_fs(kind: str) -> bool:
    """Bu filtre örnekleme frekansı (Hz) bilmeden çalışamaz mı?"""
    return kind in ("lowpass", "highpass", "notch")


def _window_n(param: float, lo: int, hi: int, odd: bool = False) -> int:
    n = int(round(param)) if np.isfinite(param) else lo
    n = max(lo, min(hi, n))
    if odd and n % 2 == 0:
        n += 1
    return n


def _sinc_lowpass(fc: float, fs: float, taps: int) -> np.ndarray:
    m = np.arange(taps) - (taps - 1) / 2
    h = np.sinc(2 * fc / fs * m) * np.blackman(taps)
    return h / h.sum()


def _fir_taps(width_hz: float, fs: float) -> int:
    """Geçiş bandı genişliğine göre katsayı sayısı (Blackman: ~5.5·fs/genişlik), tek sayı, sınırlı."""
    n = int(5.5 * fs / max(width_hz, 1e-9))
    n = max(11, min(MAX_TAPS, n))
    return n | 1


def _fast_len(n: int) -> int:
    """n'den küçük olmayan, FFT için hızlı uzunluk (2^a·3^b)."""
    best = 1 << max(0, (n - 1).bit_length())
    p3 = 1
    while p3 < best:
        p2 = p3
        while p2 < n:
            p2 *= 2
        best = min(best, p2)
        p3 *= 3
    return best


def _conv_valid(x: np.ndarray, h: np.ndarray) -> np.ndarray:
    """np.convolve(x, h, "valid") ile aynı; uzun katsayı dizisinde FFT ile (çok daha hızlı)."""
    if len(h) <= 64 or len(x) * len(h) <= 4_000_000:
        return np.convolve(x, h, mode="valid")
    n = len(x) + len(h) - 1
    nf = _fast_len(n)
    out = np.fft.irfft(np.fft.rfft(x, nf) * np.fft.rfft(h, nf), nf)
    return out[len(h) - 1:len(x)]


def _zero_phase(y: np.ndarray, h: np.ndarray) -> np.ndarray:
    """Simetrik FIR ile kaymasız süzme; kenarlar yansıtılarak uzatılır (uçlarda düşme olmasın).

    Katsayı dizisinden çok kısa parçada (kayıplar arasında kalan birkaç satır) uzatılan kısım
    çoğunlukla sabit uç değeridir; onun katkısı katsayıların toplamıyla doğrudan hesaplanır.
    Sonuç aynıdır, ama kayıplı veride yüzlerce kısa parça çok daha hızlı süzülür.
    """
    k = len(h) // 2
    L = len(y)
    if L < 2:
        return y * float(h.sum())
    pad = min(k, L - 1)
    if 2 * L > k:
        yp = np.pad(y, (pad, pad), mode="reflect")
        if pad < k:                               # kısa parça: kalan kısmı uç değerle doldur
            yp = np.pad(yp, (k - pad, k - pad), mode="edge")
        return _conv_valid(yp, h)
    # L ≤ k/2: pad = L-1; ortadaki (yansıtılmış) kısım + iki sabit uç
    mid = np.pad(y, (pad, pad), mode="reflect")
    M = len(mid)
    g = h[k - pad - (L - 1):k - pad + M]
    out = _conv_valid(g, mid[::-1])[::-1]
    c = np.concatenate(([0.0], np.cumsum(h)))
    j = np.arange(L)
    out = out + mid[0] * c[k - pad - j] + mid[-1] * (c[-1] - c[k + L + pad - j])
    return out


def _fir_kernel(kind: str, param: float, fs: float):
    """Alçak/yüksek geçiren ve çentik için katsayılar (bir kez hesaplanır). Uygulanamazsa None."""
    nyq = fs / 2
    if fs <= 0:
        return None
    if kind in ("lowpass", "highpass"):
        fc = float(param)
        if not (0 < fc < nyq):
            return None
        return _sinc_lowpass(fc, fs, _fir_taps(fc * 0.5, fs))
    if kind == "notch":
        f0 = float(param)
        bw = max(2.0, f0 * 0.1)                   # 50 Hz → 45–55 Hz bastırılır
        lo, hi = f0 - bw / 2, f0 + bw / 2
        if not (0 < lo and hi < nyq):
            return None
        taps = _fir_taps(bw / 2, fs)
        return _sinc_lowpass(hi, fs, taps) - _sinc_lowpass(lo, fs, taps)
    return None


def _filter_segment(y: np.ndarray, kind: str, param: float, h) -> np.ndarray:
    if kind == "mavg":
        n = min(_window_n(param, 1, 2001), len(y))
        if n <= 1:
            return y.copy()
        # kenarda pencere daralır (sadece var olan değerlerin ortalaması); toplam ile hızlı
        y0 = float(y[len(y) // 2])                # büyük sayılarda toplam hassasiyeti kaybolmasın
        c = np.concatenate(([0.0], np.cumsum(y - y0)))
        i = np.arange(len(y))
        lo = np.maximum(i - n // 2, 0)
        hi = np.minimum(i - n // 2 + n, len(y))
        return (c[hi] - c[lo]) / (hi - lo) + y0
    if kind == "median":
        n = _window_n(param, 3, MEDIAN_MAX, odd=True)
        if len(y) < 3:
            return y.copy()
        half = n // 2
        yp = np.pad(y, (half, half), mode="edge")
        out = np.empty_like(y)
        step = 100_000                           # büyük kayıtta bellek patlamasın diye parça parça
        for a in range(0, len(y), step):
            b = min(len(y), a + step)
            win = np.lib.stride_tricks.sliding_window_view(yp[a:b + 2 * half], n)
            out[a:b] = np.median(win, axis=1)
        return out
    if h is None:
        return y.copy()
    if kind == "lowpass":
        return _zero_phase(y, h)
    return y - _zero_phase(y, h)                   # yüksek geçiren / çentik: süzüleni çıkar


def apply_filter(y, kind: str, param: float, fs: float = 0.0) -> np.ndarray:
    """Seçilen filtreyi uygular. Boşluklar (NaN: kayıp satır) korunur; her kesintisiz parça ayrı
    süzülür, böylece kayıp bölge komşu değerlere bulaşmaz. "off" ya da uygulanamazsa veri aynen döner."""
    y = np.asarray(y, dtype=np.float64)
    if kind not in FILTERS or kind == "off" or not len(y):
        return y
    h = _fir_kernel(kind, param, fs) if filter_needs_fs(kind) else None
    if filter_needs_fs(kind) and h is None:
        return y
    fin = np.isfinite(y)
    if fin.all():
        return _filter_segment(y, kind, param, h)
    out = np.full_like(y, np.nan)
    idx = np.flatnonzero(fin)
    if not len(idx):
        return out
    cut = np.flatnonzero(np.diff(idx) > 1)
    starts = np.r_[idx[0], idx[cut + 1]]
    ends = np.r_[idx[cut], idx[-1]] + 1
    for a, b in zip(starts.tolist(), ends.tolist()):
        out[a:b] = _filter_segment(y[a:b], kind, param, h)
    return out


def filter_ready(kind: str, param: float, fs: float) -> bool:
    """Filtre bu ayarlarla gerçekten uygulanabiliyor mu? (Hz isteyen filtrede fs ve Nyquist kontrolü)"""
    if kind == "off" or kind not in FILTERS:
        return False
    if not filter_needs_fs(kind):
        return True
    if fs <= 0:
        return False
    if kind == "notch":
        bw = max(2.0, param * 0.1)
        # dar bant çok yüksek örnekleme hızında çok uzun katsayı ister; sığmazsa çentik işe yaramaz
        return param - bw / 2 > 0 and param + bw / 2 < fs / 2 and 5.5 * fs / (bw / 2) <= MAX_TAPS
    return 0 < param < fs / 2
