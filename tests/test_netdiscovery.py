# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Mehmet Can Dusgun (mhmtcndsgn) — Oskilo
"""Ağda kart bulma (keşif) testleri: sahte kart ile, bu bilgisayarın içinde (127.0.0.1)."""
import argparse
import importlib.util
import os
import sys
import threading
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from stm32logger import netdiscovery, netport, winfirewall  # noqa: E402
from stm32logger.engine import Engine, open_port  # noqa: E402
from stm32logger.session import Session  # noqa: E402

_spec = importlib.util.spec_from_file_location("sahte_kart", os.path.join(ROOT, "araclar", "ag_test_gonderici.py"))
sahte = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sahte)


def _wait(cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


class Board:
    """Sahte kartı arka planda çalıştırır (rastgele boş portlarda)."""

    def __init__(self, name="", mode="live", binary=False, dev_id="A3F2C1D0"):
        args = argparse.Namespace(name=name, id=dev_id, port=0, udp_port=0, disc_port=0, bind="127.0.0.1",
                                  host="127.0.0.1", mode=mode, binary=binary, baud=921600, islem="kart")
        self.b = sahte.FakeBoard(args)
        self.t = threading.Thread(target=self.b.run, daemon=True)
        self.t.start()

    def stop(self):
        self.b.close()
        self.t.join(timeout=2)


@pytest.fixture
def board():
    b = Board(name="Deney Düzeneği")
    d = netdiscovery.Discovery(port=b.b.disc_port, broadcast=False, interval=0.3).start()
    netdiscovery.set_shared(d)
    yield b, d
    netdiscovery.set_shared(None)
    b.stop()


def test_name_encoding_roundtrip():
    for name in ("Balans makinesi", "Deney %50 = ğüşİöç", "  boşluk  ", "a"):
        enc = netdiscovery.encode_name(name)
        assert " " not in enc and "=" not in enc
        assert netdiscovery.decode_name(enc) == name.strip()
    assert netdiscovery.encode_name("ğ") == "ğ"                       # Türkçe harf UTF-8 olarak kalır
    assert netdiscovery.decode_name("%zz%2") == "%zz%2"                # bozuk kaçış olduğu gibi kalır
    assert netdiscovery.valid_name("Balans makinesi")
    assert not netdiscovery.valid_name("")
    assert not netdiscovery.valid_name("x" * 21)
    assert not netdiscovery.valid_name("a\tb")


def test_parse_announce():
    d = netdiscovery.parse_announce(b"OSKILO 1 id=a3f2c1d0 name=Balans%20m ip=169.254.10.50 mask=255.255.0.0 "
                                    b"tcp=5000 udp=0 fmt=binary mode=dma fw=1.0.0 hw=W5500 yeni=alan\r\n", "169.254.10.50")
    assert d.id == "A3F2C1D0" and d.name == "Balans m" and d.ip == "169.254.10.50"
    assert d.tcp == 5000 and d.udp == 0 and d.fmt == "binary" and d.mode == "dma" and d.extra == {"yeni": "alan"}
    assert d.reachable(["169.254.1.212"]) and not d.reachable(["192.168.10.1"])
    assert d.label("Oskilo {short}") == "Balans m"
    assert netdiscovery.parse_announce(b"OSKILO 1 id=00AB ip=0.0.0.0", "10.0.0.7").ip == "10.0.0.7"
    # duyurudaki ip= alanı değil, paketin geldiği adres kullanılır (başka bir makineye yönlendirme yok)
    assert netdiscovery.parse_announce(b"OSKILO 1 id=00AB ip=127.0.0.1", "10.0.0.7").ip == "10.0.0.7"
    assert netdiscovery.parse_announce(b"OSKILO 1 id=00AB ip=10.0.0.9", "").ip == "10.0.0.9"
    assert netdiscovery.parse_announce(b"OSKILO 1 id=00AB", "10.0.0.7").label("K {short}") == "K 00AB"
    for bad in (b"OSKILO? 1", b"OSKILO 2 id=AB ip=1.2.3.4", b"OSKILO 1 ip=1.2.3.4", b"OSKILO 1 id=XYZ ip=1.2.3.4",
                b"merhaba", b"\xff\xfe"):
        assert netdiscovery.parse_announce(bad, "1.2.3.4") is None
    # duyuru -> çözüm -> aynı bilgi
    src = netdiscovery.Device(id="0102ABCD", ip="10.1.2.3", name="Ad ğ", fw="1.2", hw="ESP32 Wi-Fi")
    back = netdiscovery.parse_announce(netdiscovery.make_announce(src), "10.1.2.3")
    assert (back.id, back.name, back.fw, back.hw) == ("0102ABCD", "Ad ğ", "1.2", "ESP32 Wi-Fi")


def test_address_stability_and_ip_change():
    d = netdiscovery.Discovery(broadcast=False)
    a = netdiscovery.make_announce(netdiscovery.Device(id="AB12", ip="127.0.0.1"))
    b = netdiscovery.make_announce(netdiscovery.Device(id="AB12", ip="192.168.1.5"))
    d._on_packet(a, "127.0.0.1")
    for _ in range(5):                            # iki yoldan cevap: adres oynamaz
        d._on_packet(b, "192.168.1.5")
        d._on_packet(a, "127.0.0.1")
        assert d.get("AB12").ip == "127.0.0.1"
    # eski adres 3 sn'den uzun süre görülmezse yenisine geçilir (kartın IP'si değişti)
    dev = d.get("AB12")
    dev.addrs = {ip: (f - 10, last - 10) for ip, (f, last) in dev.addrs.items()}
    d._on_packet(b, "192.168.1.5")
    assert d.get("AB12").ip == "192.168.1.5"


def test_device_table_is_capped():
    d = netdiscovery.Discovery(broadcast=False)
    for i in range(netdiscovery.MAX_DEVICES + 50):           # ağdan sahte duyuru seli
        d._on_packet(f"OSKILO 1 id={i:08X}\n".encode(), "10.0.0.5")
    assert len(d.devices()) == netdiscovery.MAX_DEVICES


def test_discover_rename_identify(board):
    b, d = board
    assert _wait(lambda: d.devices(), 5)
    dev = d.devices()[0]
    assert dev.id == "A3F2C1D0" and dev.name == "Deney Düzeneği" and dev.ip == "127.0.0.1"
    assert dev.tcp == b.b.port and dev.udp == b.b.udp_port
    res = d.rename(dev.id, "Balans makinesi")
    assert res is not None and res.name == "Balans makinesi" and b.b.name == "Balans makinesi"
    assert d.identify(dev.id) is not None and b.b.blink_until > time.monotonic()
    assert d.rename("FFFFFFFF", "x") is None                         # bilinmeyen kart
    assert d.resolve("A3F2C1D0", timeout=2) is not None
    assert d.resolve("FFFFFFFF", timeout=0.5) is None


@pytest.mark.parametrize("proto", ["tcp", "udp"])
def test_connect_by_id(board, proto):
    b, d = board
    ser = open_port(netport.make_dev("a3f2c1d0", proto), 0, "live")
    assert ser.device.id == "A3F2C1D0" and ser.port.startswith(proto + "://127.0.0.1:")
    eng = Engine(ser, mode="live", session_factory=lambda: Session(mode="live"))
    eng.start()
    try:
        assert _wait(lambda: eng.current.n >= 50, 8)
        assert eng.current.missing == 0 and eng.current.bad_lines == 0
    finally:
        eng.stop()
    if proto == "udp":
        assert _wait(lambda: b.b.sub is None, 3)                     # kapatınca abonelik biter


def test_device_not_found(board):
    with pytest.raises(netport.DeviceNotFound):
        netport.open_device("dev://DEADBEEF/tcp", timeout=0.5)
    with pytest.raises(ValueError):
        netport.parse_dev("dev://xyz/tcp")
    assert netport.parse_dev("dev://ab12/UDP".replace("UDP", "udp")) == ("AB12", "udp")
    assert netport.is_net("dev://AB12/tcp") and netport.is_dev("dev://AB12/tcp") and not netport.is_dev("tcp://a:1")


def test_udp_subscription_lost_and_reconnect(board, monkeypatch):
    """UDP'de kart susarsa (kapandı) bağlantı koptu sayılır; kart geri gelince kimliğiyle yeniden bulunur."""
    b, d = board
    monkeypatch.setattr(netport, "SUB_TIMEOUT", 1.0)
    addr = netport.make_dev("A3F2C1D0", "udp")
    ser = open_port(addr, 0, "live")
    holder = {}

    def reopen():
        return open_port(addr, 0, "live")

    eng = Engine(ser, mode="live", session_factory=lambda: Session(mode="live"), reopen=reopen)
    eng.start()
    events = []

    def seen(kind):
        try:
            while True:
                events.append(eng.events_q.get_nowait()[0])
        except Exception:  # noqa: BLE001
            pass
        return kind in events

    try:
        assert _wait(lambda: eng.current.n > 10, 5)
        port = b.b.disc_port
        b.stop()                                                     # kart kapandı
        assert _wait(lambda: seen("lost"), 8)
        # kart aynı kimlikle, aynı keşif portunda geri gelir (veri portu farklı olabilir)
        args = argparse.Namespace(name="", id="A3F2C1D0", port=0, udp_port=0, disc_port=port, bind="127.0.0.1",
                                  host="127.0.0.1", mode="live", binary=False, baud=921600, islem="kart")
        nb = sahte.FakeBoard(args)
        holder["b"] = nb
        threading.Thread(target=nb.run, daemon=True).start()
        assert _wait(lambda: seen("reconnected"), 15)
        assert _wait(lambda: eng.current.n > 10, 8)
    finally:
        eng.stop()
        if "b" in holder:
            holder["b"].close()


def test_firewall_script_text():
    paths = [r"C:\Users\Mehmet Can\Python\pythonw.exe", r"C:\Users\O'Neil\python.exe"]
    script = winfirewall.build_script(paths)
    assert "'C:\\Users\\Mehmet Can\\Python\\pythonw.exe'" in script
    assert "'C:\\Users\\O''Neil\\python.exe'" in script                 # tek tırnak PowerShell'de ikilenir
    assert "-RemotePort" not in script and "-LocalPort 5000" in script     # kaynak port taklit edilebilir
    assert script.count("-RemoteAddress LocalSubnet") == 1 and "-Protocol UDP" in script
    assert "$_.Action -eq 'Block'" in script                                   # sadece engelle kuralları silinir
    import base64
    assert base64.b64decode(winfirewall.encoded(script)).decode("utf-16-le") == script
    if not winfirewall.supported():
        assert winfirewall.allow_program() == ("notwin", "")
        assert winfirewall.rule_exists() is False
    assert winfirewall.program_paths()
