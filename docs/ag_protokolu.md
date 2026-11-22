# Oskilo Ağ Protokolü — sürüm 1

Bu belge, bir kartın (STM32 + W5500, ESP32, Arduino Ethernet…) Oskilo tarafından **kendiliğinden
bulunması** ve IP yazmadan bağlanılması için uyulması gereken kuralları anlatır. Hazır örnekler:

* `firmware_ornek/stm32_w5500/` — STM32 (HAL) + W5500, `oskilo_net.c/.h` kütüphanesi
* `firmware_ornek/esp32_wifi/` — ESP32 (Arduino), Wi-Fi

Kural kısaca: **kart 5099 numaralı UDP portunu dinler, "kim var?" sorusuna cevap verir. Veri 5000
numaralı porttan TCP ya da UDP ile gider.** Mesajların hepsi düz yazıdır (UTF-8), bir mesaj bir UDP
paketidir; sondaki `\r\n` isteğe bağlıdır.

## 1. Adres alma (kartın işi)

1. Ağda adres dağıtan bir cihaz (modem, okul ağı) varsa **DHCP** ile adres alınır.
2. Birkaç saniye (önerilen 6 sn) cevap gelmezse kart **169.254.x.x** aralığından kendine adres
   seçer (maske `255.255.0.0`). Laptopa doğrudan kabloyla takıldığında Windows da aynı aralıktan adres
   aldığı için ikisi aynı ağda olur; bilgisayarda hiçbir ayar gerekmez.
3. Kablo çıkarılıp takıldığında (bağlantı ışığı söner/yanar) adres alma baştan yapılır.

Kartın **kimliği** (8 haneli onaltılık, ör. `A3F2C1D0`) değişmez: işlemcinin seri numarasından ya da
Wi-Fi MAC adresinden türetilir. Oskilo kartı IP ile değil bu kimlikle hatırlar.

## 2. Keşif — UDP 5099

### Soru (Oskilo → herkes)

```
OSKILO? 1
```

Oskilo bunu her ağ bağdaştırıcısından yayın adresine (`255.255.255.255:5099`) gönderir. Kart,
**sorunun geldiği adres ve porta** tek paketlik bir duyuru ile cevap verir.

### Duyuru (kart → soran)

```
OSKILO 1 id=A3F2C1D0 name=Balans%20makinesi ip=169.254.10.50 mask=255.255.0.0 tcp=5000 udp=5000 fmt=text mode=live fw=1.0.0 hw=NUCLEO-WL55JC1+W5500
```

| Alan | Anlamı |
|---|---|
| `id` | Değişmeyen kimlik (zorunlu) |
| `name` | Kullanıcının verdiği ad (boş olabilir). Boşluk `%20`, `%` → `%25`, `=` → `%3D` olarak yazılır; Türkçe harfler olduğu gibi (UTF-8) gider |
| `ip`, `mask` | Kartın adresi ve alt ağ maskesi (zorunlu: `ip`) |
| `tcp` | TCP veri portu; `0` = TCP yok |
| `udp` | UDP veri portu; `0` = UDP yok |
| `fmt` | `text` (satır satır sayılar) ya da `binary` (Oskilo CRC'li çerçeve) — Oskilo bunu seçili getirir |
| `mode` | `live` (canlı izleme) ya da `dma` (deney aktarımı) — Oskilo bunu seçili getirir |
| `fw`, `hw` | Yazılım sürümü ve donanım adı (bilgi için) |

Bilinmeyen alanlar yok sayılır; ileride alan eklemek eski Oskilo sürümlerini bozmaz.

### Ad verme (Oskilo → kart, tek adrese)

```
OSKILO NAME 1 id=A3F2C1D0 name=Balans%20makinesi
```

`id` kartın kendi kimliğiyse ad kalıcı hafızaya (flash / Preferences) yazılır ve sonuna `ack=name`
eklenmiş yeni duyuru gönderilir. Hafızaya yazılamazsa cevap verilmez (Oskilo "ad değiştirilemedi" der). Ad en fazla 63 bayt (kodlanmış hâliyle) olur. Kart adı çözmek zorunda değildir; aldığı
yazıyı olduğu gibi saklayıp duyuruda geri gönderir.

### Kartı göster (Oskilo → kart)

```
OSKILO BLINK 1 id=A3F2C1D0
```

Kart LED'ini ~3 saniye yakıp söndürür ve sonuna `ack=blink` eklenmiş duyuru ile cevap verir. Birden fazla kart varken hangisinin
hangisi olduğunu bulmak için.

## 3. Veri — TCP 5000 (varsayılan)

* Kart TCP sunucudur. Oskilo bağlanır, kart veriyi bu bağlantıya yazar.
* Oskilo'dan gelen baytlar kartın komutlarıdır (ör. `GONDER\r\n`).
* **Yeni bağlantı eskisinin yerini alır.** Kablo çekilip takıldığında kart eski bağlantının koptuğunu
  fark etmemiş olabilir; Oskilo yeniden bağlanınca kart eskisini kapatır.
* Oskilo bağlantı koparsa kartı kimliğiyle yeniden arar (IP değişmiş olabilir) ve saniyede bir dener.

## 4. Veri — UDP 5000 (isteğe bağlı, yüksek hız için)

Kart veriyi kimseye kendiliğinden yaymaz; sadece **abone olan** bilgisayara gönderir.

| Oskilo → kart | Kart ne yapar |
|---|---|
| `OSKILO SUB 1` | Gönderen adres:port abone olur, kart `OSKILO SUBOK 1` cevabı verir |
| `OSKILO UNSUB 1` | Abonelik biter |
| başka her şey | Kartın komutudur (ör. `GONDER`) |

* Oskilo `SUB` mesajını **saniyede bir** tekrarlar. Kart 5 saniye `SUB` almazsa göndermeyi bırakır.
* Oskilo 5 saniye `SUBOK` ya da veri alamazsa bağlantıyı kopmuş sayar ve kartı yeniden arar.
* `OSKILO ` ile başlayan paketler veri sayılmaz. Veri paketleri satır sonunda bitmelidir (metin) ya
  da tam ikili çerçeveler içermelidir.
* Oskilo önce kendisi paket gönderdiği için Windows güvenlik duvarı cevapları genelde engellemez.

## 5. Bilinen sınırlar

* Keşif yayın paketiyle çalışır: **yönlendiriciden (router) geçmez**, bazı okul / şirket Wi-Fi ağları
  da cihazları birbirinden yalıtır. Bu durumda doğrudan kablo, kendi modeminiz ya da telefon
  hotspot'u kullanın; olmazsa Oskilo'da **"Ağ — elle adres gir"** ile IP yazarak bağlanın.
* Kimlik doğrulama yoktur: aynı ağdaki herkes kartı görebilir, komut gönderebilir ya da kart gibi
  davranabilir. Laboratuvar / kapalı ağ kullanımı içindir. Oskilo, duyurudaki `ip=` alanına değil
  paketin geldiği adrese bağlanır ve en fazla 64 kartı listeler.
* Bilgisayarın Ethernet ayarı **elle IP**'ye ayarlıysa ve kart 169.254.x.x aldıysa ikisi farklı ağda
  kalır. Oskilo bunu fark edip uyarır; Ethernet ayarını "Otomatik (DHCP)" yapın.
