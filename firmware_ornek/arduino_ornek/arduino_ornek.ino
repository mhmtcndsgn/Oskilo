/*
 * Oskilo — Arduino / ESP32 / ESP8266 örneği
 * --------------------------------------------------
 * Uygulamada aynı baud hızını (115200) seç.
 *
 * MOD_DENEY 0 -> "Canlı izleme" modu: her 10 ms'de "sira,deger" gönderir.
 * MOD_DENEY 1 -> "Deney aktarımı" modu: uygulamadaki komut kutusuna GONDER
 *                yazınca N örneği sabit hızda toplar, sonra topluca gönderir:
 *                  START,N
 *                  0,512
 *                  1,515
 *                  ...
 *                  END,N
 * Sıra numarası ve START/END sayesinde uygulama kayıp satırları yakalar.
 */

#define MOD_DENEY 1

const unsigned long BAUD = 115200;
const int PIN = A0;

#if defined(ESP32)
const int N = 5000;            // ESP32'de RAM bol
#elif defined(ESP8266)
const int N = 2000;
#else
const int N = 400;             // Arduino Uno: 2 KB RAM -> küçük tampon
#endif
const unsigned long PERIYOT_US = 1000;   // 1 kHz örnekleme (uygulamada "Örnekleme frekansı" = 1000 Hz)

uint16_t buf[N];
unsigned long sira = 0;

void setup() {
  Serial.begin(BAUD);
}

void deneyiTopla() {
  unsigned long sonraki = micros();
  for (int i = 0; i < N; i++) {
    while ((long)(micros() - sonraki) < 0) { }   // sabit hız için bekle
    sonraki += PERIYOT_US;
    buf[i] = analogRead(PIN);
  }
}

void deneyiGonder() {
  Serial.print("START,"); Serial.println(N);
  for (int i = 0; i < N; i++) {
    Serial.print(i); Serial.print(','); Serial.println(buf[i]);
  }
  Serial.print("END,"); Serial.println(N);
}

void loop() {
#if MOD_DENEY
  if (Serial.available()) {
    String komut = Serial.readStringUntil('\n');
    komut.trim();
    komut.toUpperCase();
    if (komut == "GONDER" || komut == "SEND") {
      deneyiTopla();
      deneyiGonder();
    }
  }
#else
  static unsigned long t = 0;
  if (millis() - t >= 10) {
    t += 10;
    Serial.print(sira++); Serial.print(','); Serial.println(analogRead(PIN));
  }
#endif
}
