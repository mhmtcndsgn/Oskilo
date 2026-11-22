"""Kart olmadan ağ bağlantısını denemek için sahte Oskilo kartı (W5500 / ESP32 kartı gibi davranır).

    python araclar/ag_test_gonderici.py                 # sahte kart: Oskilo onu kendiliğinden bulur
    python araclar/ag_test_gonderici.py --name "Deney 1" --mode dma
    python araclar/ag_test_gonderici.py --binary         # Oskilo ikili (CRC'li) çerçeveler
    python araclar/ag_test_gonderici.py udp-gonder --host 127.0.0.1 --port 5000
                                                        # eski yöntem: "Ağ — elle adres gir" → UDP

Sahte kart docs/ag_protokolu.md'deki kurallara uyar: 5099'da "kim var?" sorusuna cevap verir, 5000'de
TCP sunucu ve UDP abonelik sunar, Oskilo'dan ad verilebilir ("Adını değiştir"). Veri, Oskilo'nun DEMO
portuyla aynıdır: canlı modda 50 Hz "sira,adc1,adc2" satırları, deney modunda 4000 örneklik
START/END aktarımı. Durdurmak için Ctrl+C ya da pencereyi kapat.
"""
import argparse
import hashlib
import os
import select
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from stm32logger import netdiscovery  # noqa: E402
from stm32logger.engine import DemoSerial  # noqa: E402

CHUNK = 1400          # UDP paket boyutu (Ethernet'e sığar)
SUB_TIMEOUT = 5.0


def _local_ip_for(peer: str) -> str:
    if peer.startswith("127."):
        return "127.0.0.1"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((peer, 1))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return "127.0.0.1"


def _split_udp(pending: bytearray, binary: bool) -> list[bytes]:
    out = []
    while pending:
        if binary or len(pending) <= CHUNK:
            cut = min(CHUNK, len(pending))
        else:
            cut = pending.rfind(b"\n", 0, CHUNK) + 1
            if cut <= 0:
                cut = CHUNK
        out.append(bytes(pending[:cut]))
        del pending[:cut]
    return out


class FakeBoard:
    def __init__(self, args):
        self.args = args
        seed = args.id or hashlib.sha1(socket.gethostname().encode()).hexdigest()[:8]
        self.id = seed.upper()
        self.name = args.name
        self.disc = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.disc.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        if os.name != "nt":
            self.disc.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.disc.bind((args.bind, args.disc_port))
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if os.name != "nt":
            self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind((args.bind, args.port))
        self.srv.listen(2)
        self.port = self.srv.getsockname()[1]
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp.bind((args.bind, self.port if args.udp_port is None else args.udp_port))
        self.udp_port = self.udp.getsockname()[1]
        self.disc_port = self.disc.getsockname()[1]
        from stm32logger.netport import no_udp_connreset
        for s in (self.disc, self.udp):
            no_udp_connreset(s)
        self.conn = None              # TCP istemcisi (yeni bağlanan eskisinin yerini alır)
        self.tcp_dev = None
        self.sub = None               # UDP abone adresi
        self.sub_t = 0.0
        self.udp_dev = None
        self.udp_pending = bytearray()
        self.sent = 0
        self.blink_until = 0.0
        self._stop = False

    def source(self) -> DemoSerial:
        a = self.args
        return DemoSerial(mode=a.mode, baud=a.baud, binary_frames=a.binary)

    def announce(self, peer: str, ack: str = "") -> bytes:
        d = netdiscovery.Device(id=self.id, ip=_local_ip_for(peer), name=self.name,
                                mask="255.0.0.0" if peer.startswith("127.") else "255.255.255.0",
                                tcp=self.port, udp=self.udp_port,
                                fmt="binary" if self.args.binary else "text", mode=self.args.mode,
                                fw="sahte-1.0", hw="Bilgisayar (sahte kart)")
        return netdiscovery.make_announce(d, ack)

    # ------------------------------------------------------------------ olaylar
    def on_disc(self) -> None:
        try:
            data, addr = self.disc.recvfrom(2048)
        except OSError:
            return
        text = data.decode("utf-8", errors="replace").strip()
        parts = text.split()
        kv = dict(p.split("=", 1) for p in parts if "=" in p)
        ack = ""
        if text.startswith("OSKILO?"):
            pass
        elif text.startswith("OSKILO NAME 1 ") and kv.get("id", "").upper() == self.id:
            name = netdiscovery.decode_name(kv.get("name", ""))
            if name != self.name:
                print(f"  yeni ad: {name!r}")
            self.name, ack = name, "name"
        elif text.startswith("OSKILO BLINK 1 ") and kv.get("id", "").upper() == self.id:
            if self.blink_until < time.monotonic():
                print("  💡 LED yanıp sönüyor (kartı göster)")
            self.blink_until, ack = time.monotonic() + 3, "blink"
        else:
            return
        try:
            self.disc.sendto(self.announce(addr[0], ack), addr)
        except OSError as e:
            print(f"  cevap gönderilemedi: {e}")

    def on_accept(self) -> None:
        conn, addr = self.srv.accept()
        if self.conn is not None:
            print("  yeni bağlantı geldi, eskisi kapatılıyor")
            self.drop_tcp()
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        conn.setblocking(False)
        self.conn, self.tcp_dev = conn, self.source()
        print(f"Oskilo TCP ile bağlandı: {addr[0]}:{addr[1]}")

    def drop_tcp(self) -> None:
        if self.conn is not None:
            try:
                self.conn.close()
            except OSError:
                pass
        self.conn, self.tcp_dev = None, None

    def on_tcp_data(self) -> None:
        try:
            cmd = self.conn.recv(4096)
        except BlockingIOError:
            return
        except OSError:
            cmd = b""
        if not cmd:
            print(f"Oskilo bağlantıyı kapattı ({self.sent} bayt gönderildi)")
            self.drop_tcp()
            return
        print(f"  komut: {cmd!r}")
        self.tcp_dev.write(cmd)

    def on_udp(self) -> None:
        try:
            data, addr = self.udp.recvfrom(4096)
        except OSError:
            return
        if data.startswith(b"OSKILO SUB "):
            if self.sub != addr:
                print(f"Oskilo UDP ile abone oldu: {addr[0]}:{addr[1]}")
                self.sub, self.udp_dev = addr, self.source()
                self.udp_pending.clear()
            self.sub_t = time.monotonic()
            self.udp.sendto(b"OSKILO SUBOK 1\n", addr)
        elif data.startswith(b"OSKILO UNSUB "):
            if self.sub == addr:
                print("Oskilo UDP aboneliği bitti")
                self.sub, self.udp_dev = None, None
        elif self.sub == addr and self.udp_dev is not None:
            print(f"  komut: {data!r}")
            self.udp_dev.write(data)

    def pump(self) -> None:
        if self.conn is not None and self.tcp_dev is not None:
            n = self.tcp_dev.in_waiting
            if n:
                data = self.tcp_dev.read(n)
                try:
                    self.conn.setblocking(True)
                    self.conn.sendall(data)
                    self.conn.setblocking(False)
                    self.sent += len(data)
                except OSError as e:
                    print(f"TCP bağlantısı bitti ({e})")
                    self.drop_tcp()
        if self.sub is not None:
            if time.monotonic() - self.sub_t > SUB_TIMEOUT:
                print("Oskilo'dan 5 sn ses yok: UDP gönderimi durdu")
                self.sub, self.udp_dev = None, None
                return
            n = self.udp_dev.in_waiting
            if n:
                self.udp_pending += self.udp_dev.read(n)
                for part in _split_udp(self.udp_pending, self.args.binary):
                    try:
                        self.udp.sendto(part, self.sub)
                        self.sent += len(part)
                    except OSError:
                        pass

    def run(self) -> None:
        label = self.name or f"Oskilo {self.id[-4:]}"
        print(f"Sahte kart hazır: {label} (kimlik {self.id})")
        print(f"  keşif UDP {self.disc_port} · veri TCP {self.port} / UDP {self.udp_port}")
        print("  Oskilo'da port listesinde '🌐 " + label + "' olarak görünür. Ctrl+C ile durdur.")
        while not self._stop:
            socks = [self.disc, self.srv, self.udp] + ([self.conn] if self.conn is not None else [])
            try:
                r, _, _ = select.select(socks, [], [], 0.005)
            except (OSError, ValueError):
                if self._stop:
                    return
                r = []
            try:
                for s in r:
                    if s is self.disc:
                        self.on_disc()
                    elif s is self.srv:
                        self.on_accept()
                    elif s is self.udp:
                        self.on_udp()
                    elif s is self.conn:
                        self.on_tcp_data()
                self.pump()
            except (OSError, ValueError, AttributeError):
                if self._stop:                     # close() başka yerden çağrıldı
                    return
                raise

    def close(self) -> None:
        self._stop = True
        self.drop_tcp()
        for s in (self.disc, self.srv, self.udp):
            try:
                s.close()
            except OSError:
                pass


def run_udp_push(args) -> None:
    """Eski yöntem: veriyi doğrudan bir adrese gönder (Oskilo: Ağ — elle adres gir → UDP)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("0.0.0.0", 0))
    s.setblocking(False)
    dest = (args.host, args.port)
    print(f"UDP gönderiliyor → {dest[0]}:{dest[1]}  (Oskilo'da: Ağ — elle adres gir → UDP → {args.port})")
    dev = DemoSerial(mode=args.mode, baud=args.baud, binary_frames=args.binary)
    pending = bytearray()
    sent, last = 0, time.monotonic()
    while True:
        try:
            cmd, _ = s.recvfrom(4096)
            print(f"  komut: {cmd!r}")
            dev.write(cmd)
        except (BlockingIOError, ConnectionResetError):
            pass
        n = dev.in_waiting
        if n:
            pending += dev.read(n)
        for part in _split_udp(pending, args.binary):
            try:
                s.sendto(part, dest)
                sent += len(part)
            except OSError:
                pass
        if time.monotonic() - last > 5:
            print(f"  {sent} bayt gönderildi")
            last = time.monotonic()
        time.sleep(0.005)


def main() -> None:
    ap = argparse.ArgumentParser(description="Oskilo sahte ağ kartı")
    ap.add_argument("islem", nargs="?", default="kart", choices=["kart", "udp-gonder", "tcp", "udp"],
                    help="kart: Oskilo'nun bulduğu sahte kart (varsayılan); udp-gonder: elle adres denemesi")
    ap.add_argument("--name", default="", help="kartın adı (Oskilo'dan da değiştirilebilir)")
    ap.add_argument("--id", default="", help="8 haneli onaltılık kimlik (varsayılan: bilgisayar adından)")
    ap.add_argument("--port", type=int, default=5000, help="veri portu (TCP ve UDP)")
    ap.add_argument("--udp-port", type=int, default=None, help=argparse.SUPPRESS)
    ap.add_argument("--disc-port", type=int, default=netdiscovery.DISCOVERY_PORT, help=argparse.SUPPRESS)
    ap.add_argument("--bind", default="0.0.0.0", help=argparse.SUPPRESS)
    ap.add_argument("--host", default="127.0.0.1", help="udp-gonder: verinin gideceği bilgisayar")
    ap.add_argument("--mode", choices=["live", "dma"], default="live")
    ap.add_argument("--binary", action="store_true", help="Oskilo ikili (CRC) çerçeveleri gönder")
    ap.add_argument("--baud", type=int, default=921600, help="veri hızı (bayt/sn = baud/10)")
    args = ap.parse_args()
    try:
        if args.islem == "udp-gonder":
            run_udp_push(args)
        else:
            try:
                board = FakeBoard(args)
            except OSError as e:
                print(f"Port açılamadı ({e}). Başka bir sahte kart ya da program zaten çalışıyor olabilir.")
                return
            board.run()
    except KeyboardInterrupt:
        print("Durduruldu.")


if __name__ == "__main__":
    main()
