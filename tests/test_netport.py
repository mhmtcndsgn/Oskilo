# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Mehmet Can Dusgun (mhmtcndsgn) — Oskilo
"""Ağ (TCP/UDP) bağlantısı testleri: gerçek soketlerle, bu bilgisayarın içinde (127.0.0.1)."""
import os
import socket
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stm32logger import binary, netport  # noqa: E402
from stm32logger.engine import Engine, open_port  # noqa: E402
from stm32logger.session import Session  # noqa: E402


def _wait(cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def _free_port(kind=socket.SOCK_STREAM) -> int:
    s = socket.socket(socket.AF_INET, kind)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class _Board:
    """Kart gibi davranan TCP sunucu: bağlanana verilen baytları yollar, gelen komutları saklar."""

    def __init__(self, chunks, port=0, close_after=False):
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", port))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        self.chunks = chunks
        self.close_after = close_after
        self.got = bytearray()
        self.conn = None
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            self.conn, _ = self.srv.accept()
        except OSError:
            return
        for c in self.chunks:
            self.conn.sendall(c)
            time.sleep(0.01)
        if self.close_after:
            self.conn.close()
            return
        self.conn.settimeout(0.2)
        while True:
            try:
                d = self.conn.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                return
            if not d:
                return
            self.got += d

    def stop(self):
        for s in (self.conn, self.srv):
            try:
                s.close()
            except Exception:
                pass


def test_parse_and_make_address():
    assert netport.parse("tcp://192.168.1.50:5000") == ("tcp", "192.168.1.50", 5000)
    assert netport.parse("UDP://:6000") == ("udp", "", 6000)
    assert netport.make_address("udp", "x", 5000) == "udp://x:5000"
    assert netport.make_address("tcp", " 10.0.0.2 ", 80) == "tcp://10.0.0.2:80"
    for bad in ("tcp://:5000", "tcp://1.2.3.4", "tcp://1.2.3.4:0", "tcp://1.2.3.4:70000", "COM3", "http://a:1"):
        with pytest.raises(ValueError):
            netport.parse(bad)
    assert netport.is_net("tcp://a:1") and netport.is_net("udp://:1") and not netport.is_net("COM3")
    assert not netport.is_net(None)


def test_tcp_engine_text_lines_and_commands():
    lines = b"".join(f"{i},{i * 2},{i * 3}\r\n".encode() for i in range(500))
    board = _Board([lines[i:i + 777] for i in range(0, len(lines), 777)])   # satırlar paket sınırında bölünür
    ser = open_port(f"tcp://127.0.0.1:{board.port}", 0, "live")
    eng = Engine(ser, mode="live", session_factory=lambda: Session(mode="live"))
    eng.start()
    try:
        assert _wait(lambda: eng.current.n == 500)
        s = eng.current
        assert s.missing == 0 and s.bad_lines == 0 and s.cols[0][499] == 998 and s.cols[1][499] == 1497
        eng.send(b"GONDER\r\n")                                    # komut karta gider
        assert _wait(lambda: board.got == b"GONDER\r\n")
    finally:
        eng.stop()
        board.stop()


def test_tcp_binary_frames():
    data = b"".join(binary.make_frame(0x02, i, (i, -i)) for i in range(300))
    board = _Board([data[i:i + 50] for i in range(0, len(data), 50)])
    ser = open_port(f"tcp://127.0.0.1:{board.port}", 0, "live", binary=True)
    eng = Engine(ser, mode="live", session_factory=lambda: Session(mode="live"), data_format="binary")
    eng.start()
    try:
        assert _wait(lambda: eng.current.n == 300)
        assert eng.current.missing == 0 and eng.current.cols[1][299] == -299
    finally:
        eng.stop()
        board.stop()


@pytest.mark.skipif(sys.platform == "win32", reason="Windows'ta SO_REUSEADDR ile aynı porta ikinci sahte kart güvenilir kurulamıyor")
def test_tcp_board_restart_reconnects():
    """Kart yeniden başlar (bağlantı kapanır): o ana kadarki veri "lost" olarak biter, yeniden bağlanılır."""
    port = _free_port()
    first = _Board([b"0,1\n1,2\n2,3\n"], port=port, close_after=True)
    ser = open_port(f"tcp://127.0.0.1:{port}", 0, "live")
    second = {}

    def reopen():
        if "b" not in second:
            first.stop()
            second["b"] = _Board([b"0,9\n1,9\n"], port=port)
        return open_port(f"tcp://127.0.0.1:{port}", 0, "live")

    eng = Engine(ser, mode="live", session_factory=lambda: Session(mode="live"), reopen=reopen)
    eng.start()
    events = []

    def seen(kind):
        try:
            while True:
                events.append(eng.events_q.get_nowait()[0])
        except Exception:
            pass
        return kind in events

    try:
        assert _wait(lambda: seen("reconnected"), 15)
        lost = eng.done_q.get(timeout=5)
        assert lost.finish_reason == "lost" and lost.n == 3
        assert _wait(lambda: eng.current.n == 2)
    finally:
        eng.stop()
        second.get("b") and second["b"].stop()


def test_tcp_connect_refused_and_timeout():
    port = _free_port()
    # Windows kapalı porta bağlanmayı birkaç kez dener: reddedildi yerine zaman aşımı gelebilir
    with pytest.raises((ConnectionRefusedError, TimeoutError) if sys.platform == "win32" else ConnectionRefusedError):
        open_port(f"tcp://127.0.0.1:{port}", 0, "live")
    t0 = time.monotonic()
    with pytest.raises(OSError):                                   # yönlendirilemeyen adres: zaman aşımı
        netport.TcpPort("10.255.255.1", 5000, connect_timeout=0.5)
    assert time.monotonic() - t0 < 3


def test_udp_engine_and_reply_to_sender():
    port = _free_port(socket.SOCK_DGRAM)
    ser = open_port(f"udp://:{port}", 0, "live")
    eng = Engine(ser, mode="live", session_factory=lambda: Session(mode="live"))
    with pytest.raises(OSError):                                   # henüz veri gelmedi: komut gidecek adres yok
        eng.send(b"X\r\n")
    eng.start()
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    tx.bind(("127.0.0.1", 0))
    tx.settimeout(2)
    try:
        for k in range(10):                                        # 10 paket, her biri 20 satır
            tx.sendto(b"".join(f"{i},{i}\n".encode() for i in range(k * 20, k * 20 + 20)), ("127.0.0.1", port))
        assert _wait(lambda: eng.current.n == 200)
        assert eng.current.missing == 0 and ser.datagrams == 10
        eng.send(b"GONDER\r\n")
        assert tx.recvfrom(100)[0] == b"GONDER\r\n"
    finally:
        eng.stop()
        tx.close()


def test_udp_port_in_use():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("0.0.0.0", 0))
    port = s.getsockname()[1]
    try:
        with pytest.raises(OSError):
            open_port(f"udp://:{port}", 0, "live")
    finally:
        s.close()


def test_local_ipv4_has_no_loopback():
    assert all(not ip.startswith("127.") for ip in netport.local_ipv4())
