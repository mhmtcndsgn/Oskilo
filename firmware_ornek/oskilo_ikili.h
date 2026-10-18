/*
 * Oskilo ikili çerçeve — gönderme kodu (tek dosya, kütüphane gerekmez)
 * =====================================================================
 *
 * STM32 (HAL), Arduino, ESP32, ESP8266, RP2040... hepsinde aynen çalışır.
 * Uygulamada  Bağlantı → Veri biçimi → "İkili (CRC)"  seçin.
 *
 * Neden ikili?
 *   - Metne göre 2–4 kat az bayt: aynı baud hızında 2–4 kat daha çok örnek.
 *   - Her çerçevede CRC16 var: kabloda bozulan veri kayda GİRMEZ, sayılır.
 *   - Sıra numarası her çerçevede: kaybolan örnek tek tek yakalanır.
 *
 * Çerçeve (küçük-endian):
 *   A5 5A | TİP | KANAL SAYISI | SIRA NO (uint32) | DEĞERLER | CRC16
 *   TİP: 0x01 uint16, 0x02 int16, 0x03 uint32, 0x04 int32, 0x05 float32
 *        0xF0 START (sıra no yerine beklenen örnek sayısı), 0xF1 END
 *   CRC16: CRC-16/CCITT-FALSE (0x1021, başlangıç 0xFFFF), TİP'ten değerlerin sonuna kadar
 *
 * Kullanım (STM32 HAL):
 *
 *   #include "oskilo_ikili.h"
 *   static void uart_yaz(const uint8_t *p, uint16_t n) { HAL_UART_Transmit(&huart2, (uint8_t *)p, n, HAL_MAX_DELAY); }
 *
 *   uint16_t adc[2] = { ... };
 *   osk_gonder_u16(uart_yaz, sira++, adc, 2);          // her örnekte bir çerçeve
 *
 *   // Deney aktarımı (DMA tamponunu sonunda topluca göndermek):
 *   osk_start(uart_yaz, N);
 *   for (uint32_t i = 0; i < N; i++) osk_gonder_u16(uart_yaz, i, &tampon[i * KANAL], KANAL);
 *   osk_end(uart_yaz, N);
 *
 * Kullanım (Arduino):   arduino_ikili/arduino_ikili.ino örneğine bakın.
 *   static void uart_yaz(const uint8_t *p, uint16_t n) { Serial.write(p, n); }
 */
#ifndef OSKILO_IKILI_H
#define OSKILO_IKILI_H

#include <stdint.h>
#include <string.h>

#define OSK_U16   0x01
#define OSK_I16   0x02
#define OSK_U32   0x03
#define OSK_I32   0x04
#define OSK_F32   0x05
#define OSK_START 0xF0
#define OSK_END   0xF1
#define OSK_MAX_KANAL 64

typedef void (*osk_yaz_fn)(const uint8_t *veri, uint16_t uzunluk);

static inline uint16_t osk_crc16(const uint8_t *p, uint16_t n, uint16_t crc)
{
    while (n--) {
        crc ^= (uint16_t)(*p++) << 8;
        for (uint8_t b = 0; b < 8; b++)
            crc = (crc & 0x8000u) ? (uint16_t)((crc << 1) ^ 0x1021u) : (uint16_t)(crc << 1);
    }
    return crc;
}

/* Genel çerçeve: tip, kanal sayısı, sıra no, değerlerin ham baytları (kanal × boyut).
   En fazla OSK_MAX_KANAL (64) kanal gönderilir; fazlası kesilir (uygulama 64'ten fazlasını kabul etmez). */
static inline void osk_cerceve(osk_yaz_fn yaz, uint8_t tip, uint8_t kanal, uint32_t sira,
                               const void *degerler, uint16_t boyut)
{
    uint8_t c[2 + 6 + OSK_MAX_KANAL * 4 + 2];
    uint16_t n = 0;
    if (kanal > OSK_MAX_KANAL) kanal = OSK_MAX_KANAL;
    uint16_t bayt = (uint16_t)(kanal * boyut);
    c[n++] = 0xA5; c[n++] = 0x5A;
    c[n++] = tip;  c[n++] = kanal;
    c[n++] = (uint8_t)(sira);       c[n++] = (uint8_t)(sira >> 8);
    c[n++] = (uint8_t)(sira >> 16); c[n++] = (uint8_t)(sira >> 24);
    if (bayt) { memcpy(&c[n], degerler, bayt); n += bayt; }   /* STM32/AVR/ESP zaten küçük-endian */
    uint16_t crc = osk_crc16(&c[2], (uint16_t)(n - 2), 0xFFFF);
    c[n++] = (uint8_t)(crc); c[n++] = (uint8_t)(crc >> 8);
    yaz(c, n);
}

static inline void osk_gonder_u16(osk_yaz_fn yaz, uint32_t sira, const uint16_t *v, uint8_t kanal)
{ osk_cerceve(yaz, OSK_U16, kanal, sira, v, 2); }

static inline void osk_gonder_i16(osk_yaz_fn yaz, uint32_t sira, const int16_t *v, uint8_t kanal)
{ osk_cerceve(yaz, OSK_I16, kanal, sira, v, 2); }

static inline void osk_gonder_u32(osk_yaz_fn yaz, uint32_t sira, const uint32_t *v, uint8_t kanal)
{ osk_cerceve(yaz, OSK_U32, kanal, sira, v, 4); }

static inline void osk_gonder_i32(osk_yaz_fn yaz, uint32_t sira, const int32_t *v, uint8_t kanal)
{ osk_cerceve(yaz, OSK_I32, kanal, sira, v, 4); }

static inline void osk_gonder_f32(osk_yaz_fn yaz, uint32_t sira, const float *v, uint8_t kanal)
{ osk_cerceve(yaz, OSK_F32, kanal, sira, v, 4); }

/* Deney aktarımının başı ve sonu (kaç örnek gönderileceği) */
static inline void osk_start(osk_yaz_fn yaz, uint32_t adet) { osk_cerceve(yaz, OSK_START, 0, adet, 0, 0); }
static inline void osk_end(osk_yaz_fn yaz, uint32_t adet)   { osk_cerceve(yaz, OSK_END, 0, adet, 0, 0); }

#endif /* OSKILO_IKILI_H */
