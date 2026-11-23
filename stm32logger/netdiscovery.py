# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Mehmet Can Dusgun (mhmtcndsgn) — Oskilo
"""Ağdaki Oskilo kartlarını kendiliğinden bulma (protokol: docs/ag_protokolu.md).

Oskilo her ağ bağdaştırıcısından yayın adresine "OSKILO? 1" sorusunu gönderir; kartlar kimlik, ad,
IP ve port bilgileriyle cevap verir. Kartlar IP ile değil değişmeyen kimlikleriyle tutulur, böylece
IP değişse de (DHCP, kablo takıp çıkarma) aynı kart yeniden bulunur.

Tek bir arka plan iş parçacığı soruları gönderir ve cevapları toplar. Arayüz devices() ile listeyi
okur; bağlanırken resolve() ile kartın güncel adresi alınır.
"""
from __future__ import annotations

import select
import socket
import threading
import time
from dataclasses import dataclass, field

from . import netport

DISCOVERY_PORT = 5099
PROTO_VERSION = "1"
QUERY = b"OSKILO? 1\n"
FRESH_S = 7.0                 # bu kadar süredir cevap vermeyen kart listeden düşer
INTERVAL_S = 2.0              # bağlı değilken soru aralığı
NAME_MAX_CHARS = 20
NAME_MAX_BYTES = 63
MAX_DEVICES = 64              # ağdan gelen sahte duyurular listeyi / belleği şişirmesin


# ------------------------------------------------------------------ ad kodlama
def encode_name(name: str) -> str:
    """Boşluk, %, = ve kontrol karakterleri %XX olur; Türkçe harfler UTF-8 olarak kalır."""
    out = bytearray()
    for b in name.encode("utf-8"):
        if b <= 0x20 or b in (0x25, 0x3D, 0x7F):
            out += f"%{b:02X}".encode()
        else:
            out.append(b)
    return out.decode("utf-8")


def decode_name(s: str) -> str:
    raw = s.encode("utf-8", errors="replace")
    out = bytearray()
    i = 0
    while i < len(raw):
        c = raw[i]
        if c == 0x25 and _ishex(raw[i + 1:i + 3]):
            out.append(int(raw[i + 1:i + 3], 16))
            i += 3
        else:
            out.append(c)
            i += 1
    return out.decode("utf-8", errors="replace").strip()


def _ishex(b: bytes) -> bool:
    return len(b) == 2 and all(ch in b"0123456789abcdefABCDEF" for ch in b)


def valid_name(name: str) -> bool:
    name = name.strip()
    return 0 < len(name) <= NAME_MAX_CHARS and len(encode_name(name).encode("utf-8")) <= NAME_MAX_BYTES \
        and all(ch.isprintable() for ch in name)


# ------------------------------------------------------------------ kart bilgisi
def _ip_int(ip: str) -> int | None:
    try:
        return int.from_bytes(socket.inet_aton(ip), "big")
    except OSError:
        return None


@dataclass
class Device:
    id: str
    ip: str
    name: str = ""
    mask: str = "255.255.255.0"
    tcp: int = 5000
    udp: int = 5000
    fmt: str = ""
    mode: str = ""
    fw: str = ""
    hw: str = ""
    src: str = ""                 # cevabın geldiği adres
    seen: float = field(default_factory=time.monotonic)
    extra: dict = field(default_factory=dict)
    addrs: dict = field(default_factory=dict)      # ip -> (ilk görülme, son görülme)

    @property
    def short_id(self) -> str:
        return self.id[-4:]

    def label(self, noname: str = "Oskilo {short}") -> str:
        return self.name or noname.format(short=self.short_id)

    def reachable(self, local_ips: list[str]) -> bool:
        """Bilgisayarın adreslerinden biri kartın alt ağında mı? (Ethernet elle IP'ye ayarlıysa hayır)"""
        ip, mask = _ip_int(self.ip), _ip_int(self.mask)
        if ip is None:
            return False
        if self.ip.startswith("127."):
            return True
        if mask is None or mask == 0:
            mask = 0xFFFFFF00
        for loc in local_ips:
            li = _ip_int(loc)
            if li is not None and (li & mask) == (ip & mask):
                return True
        return False


def parse_announce(data: bytes, src_ip: str = "") -> Device | None:
    """'OSKILO 1 id=.. ip=.. ...' duyurusunu çözer. Başka bir şeyse None."""
    try:
        text = data.decode("utf-8", errors="replace").strip()
    except Exception:  # noqa: BLE001
        return None
    parts = text.split()
    if len(parts) < 3 or parts[0] != "OSKILO" or parts[1] != PROTO_VERSION:
        return None
    kv: dict[str, str] = {}
    for p in parts[2:]:
        if "=" in p:
            k, v = p.split("=", 1)
            kv[k.lower()] = v
    dev_id = kv.get("id", "").upper()
    if not dev_id or not all(c in "0123456789ABCDEF" for c in dev_id) or len(dev_id) > 16:
        return None
    # Bağlanılacak adres, paketin GERÇEKTEN geldiği adrestir. Duyurudaki ip= alanına güvenilmez: aksi
    # hâlde ağdaki biri, Oskilo'yu başka bir bilgisayara ya da bu bilgisayardaki bir servise bağlatabilir.
    ip = src_ip if _ip_int(src_ip) is not None and src_ip != "0.0.0.0" else kv.get("ip", "")
    if _ip_int(ip) is None or ip == "0.0.0.0":
        return None

    def num(key, default):
        try:
            v = int(kv.get(key, default))
            return v if 0 <= v <= 65535 else default
        except ValueError:
            return default

    known = {"id", "ip", "name", "mask", "tcp", "udp", "fmt", "mode", "fw", "hw"}
    fmt = kv.get("fmt", "")
    mode = kv.get("mode", "")
    return Device(
        id=dev_id, ip=ip, name=decode_name(kv.get("name", ""))[:40],
        mask=kv.get("mask", "255.255.255.0"), tcp=num("tcp", 5000), udp=num("udp", 0),
        fmt=fmt if fmt in ("text", "binary") else "", mode=mode if mode in ("live", "dma") else "",
        fw=decode_name(kv.get("fw", ""))[:24], hw=decode_name(kv.get("hw", ""))[:40], src=src_ip,
        extra={k: v for k, v in kv.items() if k not in known})


def make_announce(dev: Device, ack: str = "") -> bytes:
    """Duyuru metni (sahte kart ve testler için). ack: NAME/BLINK cevabında "name" / "blink"."""
    return (f"OSKILO 1 id={dev.id} name={encode_name(dev.name)} ip={dev.ip} mask={dev.mask} "
            f"tcp={dev.tcp} udp={dev.udp} fmt={dev.fmt or 'text'} mode={dev.mode or 'live'} "
            f"fw={encode_name(dev.fw)} hw={encode_name(dev.hw)}" + (f" ack={ack}" if ack else "") + "\n"
            ).encode("utf-8")


# ------------------------------------------------------------------ keşif
class Discovery:
    """Arka planda kart arar. Tek örnek: shared()."""

    def __init__(self, port: int = DISCOVERY_PORT, extra_targets: list[tuple[str, int]] | None = None,
                 interval: float = INTERVAL_S, broadcast: bool = True):
        self.port = port
        self.interval = interval
        self.broadcast = broadcast
        self.extra_targets = list(extra_targets or [])
        self.active = True                     # False iken düzenli soru gönderilmez (bağlıyken)
        self._devs: dict[str, Device] = {}
        self._cv = threading.Condition()
        self._socks: dict[str, socket.socket] = {}     # yerel IP -> soket ("" = herhangi)
        self._sock_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._next_query = 0.0
        self._next_ifscan = 0.0
        self.local_ips: list[str] = []
        self.sent = 0
        self.errors: list[str] = []

    # ---------------------------------------------------------- yaşam döngüsü
    def start(self) -> "Discovery":
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="oskilo-discovery", daemon=True)
            self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._thread = None
        for s in self._socks.values():
            try:
                s.close()
            except OSError:
                pass
        self._socks.clear()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ---------------------------------------------------------- dışarıdan
    def query_now(self) -> None:
        self._next_query = 0.0
        self._wake.set()

    def devices(self, fresh: float = FRESH_S) -> list[Device]:
        now = time.monotonic()
        with self._cv:
            out = [d for d in self._devs.values() if now - d.seen <= fresh]
        return sorted(out, key=lambda d: (d.label().lower(), d.id))

    def get(self, dev_id: str) -> Device | None:
        with self._cv:
            return self._devs.get(dev_id.upper())

    def resolve(self, dev_id: str, timeout: float = 2.5, fresh: float = 3.0) -> Device | None:
        """Kartın güncel adresi. Yakın zamanda görülmediyse sorup cevap bekler."""
        dev_id = dev_id.upper()
        t0 = time.monotonic()
        with self._cv:
            d = self._devs.get(dev_id)
            if d is not None and t0 - d.seen <= fresh:
                return d
        self.start()
        self.query_now()
        end = t0 + timeout
        next_q = t0 + 0.5
        with self._cv:
            while True:
                d = self._devs.get(dev_id)
                if d is not None and d.seen >= t0:
                    return d
                now = time.monotonic()
                if now >= end:
                    return None
                if now >= next_q:                 # paket kaybına karşı yarım saniyede bir tekrar sor
                    self.query_now()
                    next_q = now + 0.5
                self._cv.wait(min(end - now, 0.1))

    def rename(self, dev_id: str, name: str, timeout: float = 2.0) -> Device | None:
        name = name.strip()
        msg = f"OSKILO NAME 1 id={dev_id.upper()} name={encode_name(name)}\n".encode("utf-8")
        return self._command(dev_id, msg, lambda d: d.name == name, timeout)

    def identify(self, dev_id: str, timeout: float = 1.5) -> Device | None:
        msg = f"OSKILO BLINK 1 id={dev_id.upper()}\n".encode()
        return self._command(dev_id, msg, lambda d: d.extra.get("ack") == "blink", timeout)

    def _command(self, dev_id: str, msg: bytes, ok, timeout: float) -> Device | None:
        dev_id = dev_id.upper()
        d = self.get(dev_id)
        if d is None:
            return None
        self.start()
        t0 = time.monotonic()
        end = t0 + timeout
        next_send = 0.0
        with self._cv:
            while True:
                now = time.monotonic()
                if now >= next_send:
                    self._send_to(d.ip, msg)
                    if next_send and d.src and d.src != d.ip:   # cevap gelmediyse diğer adresi de dene
                        self._send_to(d.src, msg)
                    next_send = now + 0.6
                cur = self._devs.get(dev_id)
                if cur is not None and cur.seen >= t0 and ok(cur):
                    return cur
                if now >= end:
                    return None
                self._cv.wait(min(0.1, end - now))

    def _send_to(self, ip: str, msg: bytes) -> None:
        s = self._socks.get("") or self._any_socket()
        try:
            s.sendto(msg, (ip, self.port))
        except OSError as e:
            self._note(e)

    # ---------------------------------------------------------- iş parçacığı
    def _any_socket(self) -> socket.socket:
        with self._sock_lock:
            return self._any_socket_locked()

    def _any_socket_locked(self) -> socket.socket:
        s = self._socks.get("")
        if s is None:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            except OSError:
                pass
            _no_connreset(s)
            s.bind(("0.0.0.0", 0))
            self._socks[""] = s
        return s

    def _scan_interfaces(self) -> None:
        """Her yerel IPv4 adresine bağlı bir soket: yayın o bağdaştırıcıdan çıkar (Windows'ta önemli)."""
        ips = netport.local_ipv4()
        self.local_ips = ips
        self._any_socket()
        if not self.broadcast:
            return
        for ip in list(self._socks):
            if ip and ip not in ips:
                try:
                    self._socks.pop(ip).close()
                except OSError:
                    pass
        for ip in ips:
            if ip in self._socks:
                continue
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                _no_connreset(s)
                s.bind((ip, 0))
            except OSError as e:
                s.close()
                self._note(e)
                continue
            self._socks[ip] = s

    def _send_queries(self) -> None:
        for ip, s in list(self._socks.items()):
            targets = []
            if ip and self.broadcast:
                targets.append(("255.255.255.255", self.port))
            if not ip:
                targets.append(("127.0.0.1", self.port))         # aynı bilgisayardaki sahte kart
                targets.extend(self.extra_targets)
                if self.broadcast and not self.local_ips:
                    targets.append(("255.255.255.255", self.port))
            for t in targets:
                try:
                    s.sendto(QUERY, t)
                    self.sent += 1
                except OSError as e:
                    self._note(e)

    def _note(self, e: Exception) -> None:
        self.errors = (self.errors + [str(e)])[-5:]

    def _run(self) -> None:
        while not self._stop.is_set():
            now = time.monotonic()
            try:
                if now >= self._next_ifscan:
                    self._scan_interfaces()
                    self._next_ifscan = now + 5.0
                if now >= self._next_query and (self.active or self._next_query == 0.0):
                    self._send_queries()
                    self._next_query = now + self.interval
            except OSError as e:
                self._note(e)
            socks = [s for s in self._socks.values() if s.fileno() >= 0]
            if not socks:
                self._wake.wait(0.5)
                self._wake.clear()
                continue
            try:
                r, _, _ = select.select(socks, [], [], 0.2)
            except (OSError, ValueError):
                r = []
            for s in r:
                try:
                    data, addr = s.recvfrom(2048)
                except OSError:
                    continue
                self._on_packet(data, addr[0])
            if self._wake.is_set():
                self._wake.clear()

    def _on_packet(self, data: bytes, src_ip: str) -> None:
        d = parse_announce(data, src_ip)
        if d is None:
            return
        with self._cv:
            old = self._devs.get(d.id)
            if old is None and len(self._devs) >= MAX_DEVICES:
                stale = [k for k, v in self._devs.items() if d.seen - v.seen > FRESH_S]
                if not stale:
                    return                         # liste dolu ve hepsi canlı: yeni kimliği alma
                for k in stale:
                    del self._devs[k]
            # Aynı kart iki yoldan cevap verebilir (ör. 127.0.0.1 ve ağ adresi). Listede adres oynamasın:
            # son 3 sn'de görülen adreslerden ilk bulunan kullanılır. Kartın IP'si değişirse eski
            # adres 3 sn içinde düşer ve yenisine geçilir.
            addrs = dict(old.addrs) if old is not None else {}
            first = addrs.get(d.ip, (d.seen, d.seen))[0]
            addrs[d.ip] = (first, d.seen)
            addrs = {ip: v for ip, v in addrs.items() if d.seen - v[1] <= 3.0}
            best = min(addrs.items(), key=lambda kv: kv[1][0])[0]
            if best != d.ip and old is not None and old.ip == best:
                d.ip, d.mask = old.ip, old.mask
            d.addrs = addrs
            self._devs[d.id] = d
            self._cv.notify_all()


def _no_connreset(s: socket.socket) -> None:
    netport.no_udp_connreset(s)


_shared: Discovery | None = None
_shared_lock = threading.Lock()


def shared() -> Discovery:
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = Discovery()
        return _shared


def set_shared(d: Discovery | None) -> None:
    """Testler için: ortak keşif nesnesini değiştir."""
    global _shared
    with _shared_lock:
        if _shared is not None and _shared is not d:
            _shared.stop()
        _shared = d
