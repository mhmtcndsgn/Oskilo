/*
 * Oskilo — Arduino / ESP32 ikili (CRC) örneği
 * --------------------------------------------
 * Uygulamada: Bağlantı → Veri biçimi → "İkili (CRC)", Baud = 115200.
 *
 * MOD_DENEY 0 -> "Canlı izleme" modu: her 10 ms'de A0 ve A1'i okuyup bir çerçeve gönderir.
 * MOD_DENEY 1 -> "Deney aktarımı" modu: uygulamadaki komut kutusuna GONDER yazınca
 *                N örneği sabit hızda toplar, sonra START ... END çerçeveleriyle topluca yollar.
 *
 * oskilo_ikili.h bu klasörde (firmware_ornek/oskilo_ikili.h ile aynı dosya).
 */
#include "oskilo_ikili.h"

#define MOD_DENEY 0

#if defined(ESP32)
const uint32_t N = 4000;          // ESP32'de RAM bol
#else
const uint32_t N = 300;           // Arduino Uno: 2 KB RAM -> küçük tampon (2 kanal × 2 bayt × N)
#endif
const unsigned long PERIYOT_US = 1000;   // 1 kHz (uygulamada "Örnekleme frekansı" = 1000 Hz)

static void uart_yaz(const uint8_t *p, uint16_t n) { Serial.write(p, n); }

#if MOD_DENEY
uint16_t tampon[N][2];

void deneyi_topla_ve_gonder() {
  unsigned long sonraki = micros();
  for (uint32_t i = 0; i < N; i++) {
    while ((long)(micros() - sonraki) < 0) { }   // sabit hız için bekle
    sonraki += PERIYOT_US;
    tampon[i][0] = analogRead(A0);
    tampon[i][1] = analogRead(A1);
  }
  osk_start(uart_yaz, N);
  for (uint32_t i = 0; i < N; i++) osk_gonder_u16(uart_yaz, i, tampon[i], 2);
  osk_end(uart_yaz, N);
}
#else
uint32_t sira = 0;
unsigned long son = 0;
#endif

void setup() {
  Serial.begin(115200);
}

void loop() {
#if MOD_DENEY
  if (Serial.available()) {
    String k = Serial.readStringUntil('\n');
    k.trim();
    k.toUpperCase();
    if (k == "GONDER" || k == "SEND") deneyi_topla_ve_gonder();
  }
#else
  if (millis() - son >= 10) {          // 100 Hz
    son += 10;
    uint16_t v[2] = { (uint16_t)analogRead(A0), (uint16_t)analogRead(A1) };
    osk_gonder_u16(uart_yaz, sira++, v, 2);
  }
#endif
}
