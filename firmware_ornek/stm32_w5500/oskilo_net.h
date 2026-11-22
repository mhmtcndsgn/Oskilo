/**
 * oskilo_net.h — STM32 (HAL) + W5500 için Oskilo ağ kütüphanesi
 *
 * Kartı Oskilo'nun kendiliğinden bulması için gereken her şey (docs/ag_protokolu.md):
 *   - Adres: önce DHCP (modem / okul ağı), 6 sn içinde cevap yoksa 169.254.x.x (laptopa doğrudan kablo).
 *     Kablo çıkarılıp takılınca yeniden adres alınır. Bilgisayarda hiçbir ayar gerekmez.
 *   - Kimlik ve MAC adresi işlemcinin seri numarasından türetilir (her kartta farklı, hiç değişmez).
 *   - UDP 5099: "OSKILO? 1" sorusuna cevap, ad verme (flash'a kaydedilir), "kartı göster" (LED).
 *   - Veri: TCP 5000 sunucu (varsayılan) ve UDP 5000 abonelik.
 *
 * Kullanım (main.c):
 *     #include "oskilo_net.h"
 *     static const oskilo_net_config_t net = { .hw = "NUCLEO-WL55JC1+W5500", .fw = "1.0.0" };
 *     oskilo_net_init(&net);                   // MX_SPI1_Init() sonrasında
 *     while (1) {
 *         oskilo_net_poll();                   // ana döngüde sık sık (bekletme yapmaz)
 *         if (oskilo_net_connected())
 *             oskilo_net_printf("%lu,%u,%u\r\n", sira++, adc1, adc2);
 *     }
 *
 * Gerekenler: WIZnet ioLibrary_Driver (Ethernet/ ve Internet/DHCP/ klasörleri) ve
 * projede _WIZCHIP_=W5500 tanımı. Ayrıntılar README.md'de.
 */
#ifndef OSKILO_NET_H
#define OSKILO_NET_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ------------------------------------------------------------------ donanım ayarları
 * CubeMX'teki adlarla değiştirin ya da derleyici tanımı olarak verin (-DOSKILO_CS_PIN=...).
 * Varsayılan: NUCLEO Arduino konnektörü — D13 SCK (PA5), D12 MISO (PA6), D11 MOSI (PA7), D10 CS (PA4).
 */
#ifndef OSKILO_SPI
#define OSKILO_SPI            hspi1          /* CubeMX'in oluşturduğu SPI tutamağı */
#endif
#ifndef OSKILO_CS_PORT
#define OSKILO_CS_PORT        GPIOA
#endif
#ifndef OSKILO_CS_PIN
#define OSKILO_CS_PIN         GPIO_PIN_4
#endif
/* W5500 RST ucunu bir GPIO'ya bağladıysanız açın (bağlamadıysanız yazılımla sıfırlanır): */
/* #define OSKILO_RST_PORT    GPIOA */
/* #define OSKILO_RST_PIN     GPIO_PIN_9 */

/* ------------------------------------------------------------------ ağ ayarları */
#ifndef OSKILO_DISCOVERY_PORT
#define OSKILO_DISCOVERY_PORT 5099
#endif
#ifndef OSKILO_DHCP_TIMEOUT_MS
#define OSKILO_DHCP_TIMEOUT_MS 6000          /* bu sürede DHCP cevabı yoksa 169.254.x.x */
#endif

/* ------------------------------------------------------------------ ad (flash'ta saklanır)
 * Varsayılan: STM32WL55JC (256 KB flash) son sayfası, 0x0803F800, 2 KB.
 * Bu sayfayı programın kullanmaması için bağlayıcı (linker) dosyasında FLASH LENGTH'i 2 KB azaltın
 * (README'ye bakın). Flash'a yazmak istemiyorsanız OSKILO_NAME_IN_FLASH'ı 0 yapın: ad sadece kart
 * açık kaldığı sürece hatırlanır.
 */
#ifndef OSKILO_NAME_IN_FLASH
#define OSKILO_NAME_IN_FLASH  1
#endif
#ifndef OSKILO_NAME_FLASH_PAGE
#define OSKILO_NAME_FLASH_PAGE 127
#endif
#ifndef OSKILO_NAME_FLASH_ADDR
#define OSKILO_NAME_FLASH_ADDR 0x0803F800UL
#endif

#define OSKILO_NAME_MAX       63             /* kodlanmış ad, bayt */

typedef struct {
    const char *hw;          /* donanım adı, ör. "NUCLEO-WL55JC1 + W5500" (Oskilo kart bilgisinde gösterir) */
    const char *fw;          /* yazılım sürümü, ör. "1.0.0" */
    const char *fmt;         /* "text" (satır satır sayılar) ya da "binary" (Oskilo CRC çerçevesi); NULL = text */
    const char *mode;        /* "live" (canlı izleme) ya da "dma" (deney aktarımı); NULL = live */
    uint16_t tcp_port;       /* 0 = 5000 */
    uint16_t udp_port;       /* 0 = 5000 */
} oskilo_net_config_t;

/* 0: hazır, -1: W5500 cevap vermiyor (SPI / kablolama / besleme) */
int  oskilo_net_init(const oskilo_net_config_t *cfg);

/* Ana döngüde sık sık çağırın. Hiç beklemez (DHCP, keşif, bağlantılar, komutlar). */
void oskilo_net_poll(void);

/* Veriyi alacak biri var mı? (TCP bağlantısı ya da UDP abonesi) */
int  oskilo_net_connected(void);

/* Veriyi gönderir. Dönüş: gönderilen bayt; 0 = kimse yok ya da gönderme tamponu dolu (veri atlandı,
 * Oskilo sıra numarasından kaybı görür). Bir satırı tek seferde gönderin. */
int  oskilo_net_send(const void *data, uint16_t len);
int  oskilo_net_printf(const char *fmt, ...);

/* Kartın kimliği (8 haneli onaltılık) ve IP adresi (0.0.0.0 = henüz yok) */
const char *oskilo_net_id(void);
void oskilo_net_get_ip(uint8_t ip[4]);
int  oskilo_net_link_up(void);

/* ------------------------------------------------------------------ isteğe bağlı geri çağırmalar
 * Kendi kodunuzda aynı adla yazarsanız onlar kullanılır (zayıf / weak tanımlı).
 */
void oskilo_net_on_command(const char *line);           /* Oskilo'dan gelen satır, ör. "GONDER" */
void oskilo_net_on_identify(int led_on);                 /* "Kartı göster": LED'i yak (1) / söndür (0) */
void oskilo_net_on_address(const uint8_t ip[4], int dhcp); /* adres alındı (dhcp=0: 169.254.x.x) */

#ifdef __cplusplus
}
#endif
#endif /* OSKILO_NET_H */
