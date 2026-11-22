# STM32 + W5500 → Oskilo (Ethernet, IP yazmadan)

Bu örnekte kart Ethernet kablosuyla laptopa (ya da modeme) takılır. **Oskilo kartı kendiliğinden
bulur.** Kartın IP'sini yazmanız ya da Windows'ta ağ ayarı yapmanız gerekmez. Örnek
NUCLEO-WL55JC1 için hazırlandı, ama HAL kullanan her STM32'de çalışır.

| Dosya | Ne işe yarar |
|---|---|
| `oskilo_net.h / .c` | Ağ kütüphanesi: adres alma, keşif, TCP/UDP, ad verme, "kartı göster" |
| `main_ornek.c` | `main.c`'ye kopyalanacak parçalar (50 Hz örnek veri, komutlar, LED) |

Kuralların tamamı: [`docs/ag_protokolu.md`](../../docs/ag_protokolu.md)

## 1. Kablolama

| W5500 modülü | NUCLEO (Arduino konnektörü) | STM32 pini |
|---|---|---|
| 3.3V (VCC) | 3V3 | — |
| GND | GND | — |
| SCK | D13 | PA5 |
| MISO | D12 | PA6 |
| MOSI | D11 | PA7 |
| CS (SCS) | D10 | PA4 |
| RST | (isteğe bağlı) | boş bir GPIO |
| INT | bağlanmaz | — |

Modülü **5V ile beslemeyin**. Modülünüzde ayrıca 5V girişi varsa o ayrı bir durumdur. Başka pinler
kullandıysanız `oskilo_net.h` dosyasının başındaki `OSKILO_CS_PORT / OSKILO_CS_PIN` satırlarını
değiştirin.

## 2. CubeMX (STM32CubeIDE)

1. **SPI1**:
   * Mode: *Full-Duplex Master*, Hardware NSS: *Disable*.
   * Data Size *8 Bits*, *MSB First*.
   * CPOL *Low*, CPHA *1 Edge* (SPI mod 0).
   * Prescaler: SPI hızı 20 MHz'in altında olmalı. 48 MHz'de `/4` = 12 MHz iyi bir başlangıç.
2. **PA4** → *GPIO_Output*, başlangıç değeri *High* (CS).
3. **LED**: "Kartı göster" için PB15 (mavi LED) → *GPIO_Output*. Kartı seçerek proje açtıysanız
   zaten tanımlıdır.
4. Kodu üretin (*Generate Code*).

## 3. WIZnet ioLibrary

1. <https://github.com/Wiznet/ioLibrary_Driver> adresinden indirin (*Code → Download ZIP*).
2. Projeye şu dosyaları kopyalayın, örneğin `Drivers/ioLibrary/` altına:
   * `Ethernet/socket.c`, `socket.h`, `wizchip_conf.c`, `wizchip_conf.h`
   * `Ethernet/W5500/` klasörü
   * `Internet/DHCP/dhcp.c`, `dhcp.h`
3. *Project → Properties → C/C++ Build → Settings → MCU GCC Compiler*:
   * **Include paths**: yukarıdaki üç klasörü ekleyin.
   * **Preprocessor → Define symbols**: `_WIZCHIP_=W5500` ekleyin. **Önemli:** kütüphanenin yeni
     sürümleri varsayılan olarak W6300 seçili gelir. Bu tanım yoksa derleme bilerek hata verir.
4. `oskilo_net.h`'yi `Core/Inc`, `oskilo_net.c`'yi `Core/Src` klasörüne kopyalayın.

## 4. Kartın adının saklandığı flash sayfası

Oskilo'dan karta verilen ad, flash'ın **son sayfasına** (STM32WL55JC'de `0x0803F800`, 2 KB) yazılır.
Programın o sayfayı kullanmaması için `STM32WL55JCIX_FLASH.ld` dosyasında FLASH satırını 2 KB
küçültün:

```
FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 254K     /* 256K idi */
```

Başka bir STM32 kullanıyorsanız `OSKILO_NAME_FLASH_PAGE` ve `OSKILO_NAME_FLASH_ADDR` değerlerini o
işlemcinin son sayfasına göre ayarlayın. Bununla uğraşmak istemiyorsanız `OSKILO_NAME_IN_FLASH`'ı
`0` yapın; ad bu durumda yalnızca kart kapanana kadar hatırlanır.

## 5. main.c

`main_ornek.c` içindeki blokları `main.c`'de aynı adlı `USER CODE` bölümlerine kopyalayın:

* Includes
* PV
* 0
* 2
* WHILE

Örnek 50 Hz'de `sira,adc1,adc2` satırları gönderir. Kendi ölçümünüz için `oskilo_net_printf(...)`
satırını değiştirin.

Kütüphanenin dışarıya açtığı fonksiyonlar:

```c
oskilo_net_init(&ayar);        // MX_SPI1_Init()'ten sonra, bir kez
oskilo_net_poll();             // ana döngüde sık sık (hiç beklemez)
oskilo_net_connected();        // veriyi alacak biri var mı
oskilo_net_send(buf, len);     // ham bayt (ör. Oskilo ikili çerçeveleri)
oskilo_net_printf("...");      // metin satırı
```

Ana döngüde `HAL_Delay` gibi uzun beklemeler kullanmayın. Zamanlamayı örnekteki gibi
`HAL_GetTick()` ile yapın.

## 6. Deneme

1. Kartı programlayın. Ethernet kablosunu karttan laptopa takın.
2. Oskilo'yu açın. Birkaç saniye içinde port listesinde **🌐 Oskilo XXXX** görünür.
   * Windows doğrudan kablo bağlantısında adresi 10–60 sn içinde alır. İlk seferde biraz bekleyin.
3. Kartı seçin, **TCP** seçili kalsın, **Bağlan**'a basın.
4. **✎ Adını değiştir** ile karta bir ad verin. Ad kartın hafızasına yazılır ve her bilgisayarda aynı
   görünür.
5. **💡 Kartı göster** düğmesine basınca kartın LED'i 3 saniye yanıp söner.

## 7. Sorun giderme

| Belirti | Olası sebep |
|---|---|
| LED hızlı yanıp sönüyor | `oskilo_net_init` W5500'ü göremedi: SPI kabloları, 3.3V, CS pini, SPI mod 0 |
| Ethernet girişinin ışıkları yanmıyor | Kablo / modül beslemesi |
| Oskilo kartı bulamıyor | Oskilo'da **Ağ — elle adres gir → Kart bulunamıyor mu?** penceresi; Windows güvenlik duvarı için **Oskilo'ya izin ver** |
| Oskilo "farklı ağ ayarı" uyarısı veriyor | Bilgisayarın Ethernet ayarı elle IP'ye ayarlı: *Otomatik (DHCP)* yapın. Kart modeme takılıysa ve modem geç cevap verdiyse kabloyu çıkarıp takın |
| Kart okul Wi-Fi'ı / şirket ağı üzerinden bulunmuyor | Bu ağlar yayın paketlerini engeller: doğrudan kablo ya da kendi modeminizi kullanın |

## Teknik notlar

* **Kimlik ve MAC**: işlemcinin 96 bitlik seri numarasından türetilir. Kimlik
  `oskilo_net_id()` ile okunur, MAC `02:4F:...` biçimindedir (yerel yönetilen). Bu yüzden iki kart
  aynı ağda çakışmaz.
* **Adres**:
  * Önce 6 sn DHCP denenir (`OSKILO_DHCP_TIMEOUT_MS`), cevap gelmezse 169.254.x.x kullanılır. Modem
    çok geç cevap verdiyse kart 169.254.x.x'te kalır: kabloyu çıkarıp takın.
  * Adres çakışması fark edilirse başka bir adres seçilir.
  * Kablo çıkarılıp takılınca adres alma baştan yapılır.
* **TCP**:
  * İki soket aynı portu dinler, yeni bağlantı eskisinin yerini alır.
  * 10 sn'de bir "orada mısın" (keep-alive) paketi gider.
  * Gönderme tamponu doluysa `oskilo_net_send` beklemez, 0 döner. O satır atlanır ve Oskilo kaybı
    sıra numarasından görür.
* **UDP**: veri sadece abone olan bilgisayara gider. Oskilo 5 sn sessiz kalırsa gönderim durur.
* W5500 soketleri:
  * 0: DHCP
  * 1: keşif
  * 2–3: TCP
  * 4: UDP
  * 5–7 boş (kendi işleriniz için kullanabilirsiniz, tampon boyutu 0 ayrıldı: `wizchip_init`
    satırını değiştirin).
