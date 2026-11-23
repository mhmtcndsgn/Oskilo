# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Mehmet Can Dusgun (mhmtcndsgn) — Oskilo
"""Ağ üzerinden veri: TCP ve UDP (ör. W5500, ESP32 Wi-Fi, ENC28J60).

Bu sınıflar pyserial'ın Serial nesnesiyle aynı küçük arayüzü sunar (in_waiting, read, write,
close, is_open). Böylece okuma motoru, kayıp tespiti, ikili çerçeveler, kayıt ve alarmlar hiç
değişmeden ağdan gelen veriyle de çalışır.

Adres biçimi (port kutusunun arka planda kullandığı yazı):
    dev://A3F2C1D0/tcp        ağda bulunan kart (kimliğiyle); IP her bağlanışta yeniden bulunur
    dev://A3F2C1D0/udp        aynı kart, UDP abonelikle (docs/ag_protokolu.md)
    tcp://192.168.1.50:5000   Oskilo karta bağlanır (kart TCP sunucusu, elle adres)
    udp://:5000               Oskilo bilgisayarın 5000 numaralı UDP portunu dinler (kart gönderir)

TCP'de karşı taraf bağlantıyı kapatırsa ya da kablo çekilirse okuma OSError verir; motor bunu
"bağlantı koptu" sayar ve (açıksa) saniyede bir yeniden bağlanmayı dener. UDP bağlantısızdır:
veri kesilmesi koptu sayılmaz, sadece veri gelmez.
"""
from __future__ import annotations

import re
import select
import socket
import sys
import time

CONNECT_TIMEOUT = 2.0
RECV_CHUNK = 65536
RCVBUF = 4 * 1024 * 1024
UDP_MAX = 65535

_RE = re.compile(r"^(tcp|udp)://(\[[^\]]*\]|[^:/\s]*):(\d{1,5})$", re.I)


SUB_MSG = b"OSKILO SUB 1\n"
UNSUB_MSG = b"OSKILO UNSUB 1\n"
SUB_EVERY = 1.0             # abonelik saniyede bir tazelenir
SUB_TIMEOUT = 5.0           # bu kadar süre kartten ses gelmezse bağlantı koptu sayılır

_RE_DEV = re.compile(r"^dev://([0-9A-Fa-f]{1,16})/(tcp|udp)$")


class DeviceNotFound(OSError):
    """Kart ağda bulunamadı (kapalı, kablo çıkık ya da başka ağda)."""

    def __init__(self, dev_id: str):
        super().__init__(f"kart ağda bulunamadı: {dev_id}")
        self.dev_id = dev_id


def is_net(port: str | None) -> bool:
    return bool(port) and str(port).lower().startswith(("tcp://", "udp://", "dev://"))


def is_dev(port: str | None) -> bool:
    return bool(port) and str(port).lower().startswith("dev://")


def make_dev(dev_id: str, proto: str) -> str:
    return f"dev://{dev_id.upper()}/{'udp' if proto == 'udp' else 'tcp'}"


def parse_dev(address: str) -> tuple[str, str]:
    """'dev://ID/tcp' -> ('ID', 'tcp'). Hatalıysa ValueError."""
    m = _RE_DEV.match(str(address).strip())
    if not m:
        raise ValueError(f"geçersiz kart adresi: {address!r}")
    return m.group(1).upper(), m.group(2).lower()


def make_address(proto: str, host: str, port: int) -> str:
    proto = proto.lower()
    host = (host or "").strip()
    if proto == "udp":
        return f"udp://{host}:{int(port)}"          # host boş: bütün ağ kartlarında dinle
    return f"tcp://{host}:{int(port)}"


def parse(address: str) -> tuple[str, str, int]:
    """'tcp://host:port' -> ('tcp', host, port). Hatalıysa ValueError."""
    m = _RE.match(str(address).strip())
    if not m:
        raise ValueError(f"geçersiz ağ adresi: {address!r}")
    proto, host, port = m.group(1).lower(), m.group(2).strip("[]"), int(m.group(3))
    if not 1 <= port <= 65535:
        raise ValueError(f"geçersiz port: {port}")
    if proto == "tcp" and not host:
        raise ValueError("TCP için kartın IP adresi gerekli")
    return proto, host, port


def no_udp_connreset(s: socket.socket) -> None:
    """Windows: karşı port kapalıyken gelen ICMP "port ulaşılamaz" sonraki okumayı hata ile bozmasın.

    Python'un socket.ioctl'ü SIO_UDP_CONNRESET'i bilmez; doğrudan WSAIoctl çağrılır.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        SIO_UDP_CONNRESET = 0x9800000C
        ws2 = ctypes.WinDLL("ws2_32")
        ws2.WSAIoctl.argtypes = [ctypes.c_size_t, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                 ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                 ctypes.c_void_p, ctypes.c_void_p]
        ws2.WSAIoctl.restype = ctypes.c_int
        off = wintypes.BOOL(False)
        ret = wintypes.DWORD(0)
        ws2.WSAIoctl(s.fileno(), SIO_UDP_CONNRESET, ctypes.byref(off), ctypes.sizeof(off), None, 0,
                     ctypes.byref(ret), None, None)
    except Exception:  # noqa: BLE001  olmazsa okuma döngüleri ConnectionResetError'u zaten yutar
        pass


class _NetBase:
    """pyserial benzeri arayüz. Gelen baytlar iç tamponda bekler."""

    def __init__(self):
        self.timeout = 0.05
        self.is_open = True
        self._buf = bytearray()
        self.sock: socket.socket | None = None

    # pyserial uyumlu arayüz ------------------------------------------------
    @property
    def in_waiting(self) -> int:
        self._pull(0.0)
        return len(self._buf)

    def read(self, size: int = 1) -> bytes:
        if not self._buf:
            self._pull(self.timeout)
        k = min(max(1, size), len(self._buf))
        out = bytes(self._buf[:k])
        del self._buf[:k]
        return out

    def close(self) -> None:
        self.is_open = False
        s, self.sock = self.sock, None
        if s is not None:
            try:
                s.close()
            except OSError:
                pass

    def set_buffer_size(self, **_):
        pass

    def reset_input_buffer(self) -> None:
        self._buf.clear()

    # alt sınıflar --------------------------------------------------------
    def _pull(self, timeout: float) -> None:
        raise NotImplementedError

    def _ready(self, timeout: float) -> bool:
        if self.sock is None or not self.is_open:
            raise OSError("bağlantı kapalı")
        r, _, _ = select.select([self.sock], [], [], timeout)
        return bool(r)


def _keepalive(s: socket.socket) -> None:
    """Kablo çekilince ya da kart kapanınca bağlantının ölü olduğu birkaç saniyede anlaşılsın."""
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        if sys.platform == "win32":
            s.ioctl(socket.SIO_KEEPALIVE_VALS, (1, 3000, 1000))
        else:
            for name, val in (("TCP_KEEPIDLE", 3), ("TCP_KEEPINTVL", 1), ("TCP_KEEPCNT", 3)):
                if hasattr(socket, name):
                    s.setsockopt(socket.IPPROTO_TCP, getattr(socket, name), val)
    except (OSError, AttributeError, ValueError):
        pass


class TcpPort(_NetBase):
    """Oskilo istemci, kart sunucu."""

    def __init__(self, host: str, port: int, connect_timeout: float = CONNECT_TIMEOUT):
        super().__init__()
        self.port = f"tcp://{host}:{port}"
        s = socket.create_connection((host, port), timeout=connect_timeout)
        try:
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, RCVBUF)
        except OSError:
            pass
        _keepalive(s)
        s.settimeout(1.0)                         # yazma (komut gönderme) en fazla 1 sn bekler
        self.sock = s

    def _pull(self, timeout: float) -> None:
        while self._ready(timeout):
            data = self.sock.recv(RECV_CHUNK)
            if not data:
                raise OSError("kart bağlantıyı kapattı")
            self._buf += data
            timeout = 0.0
            if len(self._buf) >= RECV_CHUNK * 4:
                break

    def write(self, data: bytes) -> int:
        if self.sock is None:
            raise OSError("bağlantı kapalı")
        self.sock.sendall(data)
        return len(data)


class UdpPort(_NetBase):
    """Oskilo bir UDP portunu dinler. Komutlar son veri gönderen adrese yollanır.

    remote + subscribe verilirse (bulunan kart): Oskilo karta saniyede bir "OSKILO SUB 1" yollar,
    kart veriyi sadece bu bilgisayara gönderir. 5 sn kartten ses gelmezse okuma OSError verir.
    """

    def __init__(self, host: str, port: int, remote: tuple[str, int] | None = None, subscribe: bool = False):
        super().__init__()
        self.port = f"udp://{remote[0]}:{remote[1]}" if remote else f"udp://{host}:{port}"
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, RCVBUF)
        except OSError:
            pass
        if sys.platform == "win32":                # başka bir program aynı portu "paylaşarak" çalmasın
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            except (OSError, AttributeError):
                pass
        no_udp_connreset(s)
        try:
            s.bind((host or "0.0.0.0", port))
        except OSError:
            s.close()
            raise
        self.sock = s
        self.peer: tuple | None = remote
        self.datagrams = 0
        self._sub = bool(subscribe and remote)
        self._last_heard = time.monotonic()
        self._next_sub = 0.0
        if self._sub:
            self._keepalive()

    def _keepalive(self) -> None:
        now = time.monotonic()
        if now >= self._next_sub:
            self._next_sub = now + SUB_EVERY
            try:
                self.sock.sendto(SUB_MSG, self.peer)
            except OSError:
                pass                                # ağ geçici yok; zaman aşımı karar verir
        if now - self._last_heard > SUB_TIMEOUT:
            raise OSError("kart cevap vermiyor")

    def _pull(self, timeout: float) -> None:
        if self._sub:
            if self.sock is None or not self.is_open:
                raise OSError("bağlantı kapalı")
            self._keepalive()
        while self._ready(timeout):
            try:
                data, addr = self.sock.recvfrom(UDP_MAX)
            except ConnectionResetError:           # (Windows ICMP; yukarıda kapatıldı, yine de)
                continue
            if self._sub:
                if addr[0] != self.peer[0]:
                    continue                        # başka bir cihaz: yok say
                self._last_heard = time.monotonic()
                if data.startswith(b"OSKILO "):    # protokol mesajı (SUBOK), veri değil
                    continue
            else:
                self.peer = addr
            self.datagrams += 1
            self._buf += data
            timeout = 0.0
            if len(self._buf) >= RECV_CHUNK * 4:
                break

    def close(self) -> None:
        if self._sub and self.sock is not None:
            try:
                self.sock.sendto(UNSUB_MSG, self.peer)
            except OSError:
                pass
        super().close()

    def write(self, data: bytes) -> int:
        if self.sock is None:
            raise OSError("bağlantı kapalı")
        if self.peer is None:
            raise OSError("karttan henüz veri gelmedi; komutun gideceği adres bilinmiyor")
        return self.sock.sendto(data, self.peer)


def open_net(address: str):
    if is_dev(address):
        return open_device(address)
    proto, host, port = parse(address)
    return TcpPort(host, port) if proto == "tcp" else UdpPort(host, port)


def open_device(address: str, timeout: float = 2.5):
    """Ağda bulunan karta kimliğiyle bağlanır. IP her seferinde yeniden bulunur."""
    from . import netdiscovery

    dev_id, proto = parse_dev(address)
    d = netdiscovery.shared().resolve(dev_id, timeout=timeout)
    if d is None:
        raise DeviceNotFound(dev_id)
    if proto == "udp" and d.udp:
        p = UdpPort("", 0, remote=(d.ip, d.udp), subscribe=True)
    elif d.tcp:
        p = TcpPort(d.ip, d.tcp)
    else:                                          # kart sadece UDP biliyor
        p = UdpPort("", 0, remote=(d.ip, d.udp), subscribe=True)
    p.device = d
    return p


def local_ipv4() -> list[str]:
    """Bu bilgisayarın IPv4 adresleri (kartın UDP ile göndereceği hedef için gösterilir)."""
    ips: list[str] = []
    try:                                           # varsayılan çıkış adresi (paket gönderilmez)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("10.255.255.255", 1))
            ips.append(s.getsockname()[0])
        finally:
            s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.append(info[4][0])
    except OSError:
        pass
    out = []
    for ip in ips:
        if ip and not ip.startswith("127.") and ip not in out:
            out.append(ip)
    return out
