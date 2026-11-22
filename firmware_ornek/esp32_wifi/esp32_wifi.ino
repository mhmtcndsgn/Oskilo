/*
 * Oskilo — ESP32 Wi-Fi örneği (IP yazmadan)
 * --------------------------------------------------
 * Oskilo bu kartı ağda kendiliğinden bulur: port listesinde "🌐 Oskilo XXXX" olarak görünür.
 * Kurallar: docs/ag_protokolu.md
 *
 * İki çalışma şekli:
 *   1) WIFI_AG boş (varsayılan): ESP32 kendi Wi-Fi ağını açar → "Oskilo-XXXX", şifre "oskilo123".
 *      Laptopu bu ağa bağlayın, Oskilo'yu açın. Modem gerekmez. (Bu sırada laptopun interneti olmaz.)
 *   2) WIFI_AG dolu: ESP32 o ağa bağlanır (ev modemi, telefon hotspot'u). Laptop da aynı ağda olmalı.
 *      Okul / şirket Wi-Fi'ları cihazları birbirinden yalıttığı için orada 1. yolu kullanın.
 *
 * Gönderilen veri: 50 Hz "sira,adc1,adc2" satırları (Oskilo: Canlı izleme, Metin).
 * Oskilo'dan gelen komutlar: DURDUR / BASLA / SIFIRLA (Terminal ya da hızlı komut düğmeleri).
 * Arduino IDE: Araçlar → Kart → ESP32 Dev Module (ESP32 kart paketi 2.x ya da 3.x).
 */
#include <WiFi.h>
#include <WiFiUdp.h>
#include <Preferences.h>

// ------------------------------------------------------------------ ayarlar
const char *WIFI_AG = "";              // bağlanılacak Wi-Fi adı; boşsa ESP32 kendi ağını açar
const char *WIFI_SIFRE = "";
// ESP32'nin kendi ağının şifresi (en az 8 karakter). Herkes bu örneği görebildiği için kendi şifrenizle
// DEĞİŞTİRİN: ağa giren biri verinizi okuyabilir ve karta komut gönderebilir.
const char *AP_SIFRE = "oskilo123";

const uint16_t KESIF_PORT = 5099;      // Oskilo bu porta "kim var?" diye sorar
const uint16_t VERI_PORT = 5000;       // TCP sunucu ve UDP abonelik
const char *YAZILIM = "1.0.0";
const char *DONANIM = "ESP32 Wi-Fi";

const int ADC1_PIN = 34;               // ESP32 DevKit; S3 / C3 kartlarda kendi ADC pinlerinizi yazın
const int ADC2_PIN = 35;
#ifdef LED_BUILTIN
const int LED_PIN = LED_BUILTIN;       // "Kartı göster" düğmesinde yanıp söner
#else
const int LED_PIN = 2;
#endif

// ------------------------------------------------------------------ durum
WiFiUDP kesif;
WiFiUDP veri;
WiFiServer sunucu(VERI_PORT);
WiFiClient istemci;
Preferences hafiza;

char kimlik[9];                        // 8 haneli onaltılık; Wi-Fi MAC'inden, hiç değişmez
String ad;                             // Oskilo'dan verilen ad (kodlanmış hâliyle saklanır)
bool apModu = false;

IPAddress aboneIP;                     // UDP abonesi
uint16_t aboneAdres = 0;
uint32_t aboneZaman = 0;

uint32_t sira = 0, sonraki = 0;
uint32_t yanipBitis = 0, yanipSonraki = 0;
bool ledDurum = false, gonder = true;
String komut;

// ------------------------------------------------------------------ yardımcılar
// Boşluk, %, = ve kontrol karakterleri %XX olur (duyurudaki alan değerleri)
String kodla(const char *s) {
  String o;
  char t[4];
  for (; *s; s++) {
    uint8_t c = (uint8_t)*s;
    if (c <= 0x20 || c == '%' || c == '=' || c == 0x7F) {
      snprintf(t, sizeof t, "%%%02X", c);
      o += t;
    } else {
      o += (char)c;
    }
  }
  return o;
}

// "anahtar=değer" alanı (önünde boşluk olmalı); yoksa "\x01" döner
String alan(const String &m, const char *anahtar) {
  String k = String(" ") + anahtar + "=";
  int i = m.indexOf(k);
  if (i < 0) return String("\x01");
  i += k.length();
  int j = m.indexOf(' ', i);
  return m.substring(i, j < 0 ? m.length() : j);
}

IPAddress benimIP() { return apModu ? WiFi.softAPIP() : WiFi.localIP(); }
IPAddress maske() { return apModu ? IPAddress(255, 255, 255, 0) : WiFi.subnetMask(); }

void duyur(IPAddress ip, uint16_t port, const char *onay = nullptr) {
  IPAddress a = benimIP(), m = maske();
  String s = "OSKILO 1 id=";
  s += kimlik;
  s += " name=" + ad;
  s += " ip=" + a.toString() + " mask=" + m.toString();
  s += " tcp=" + String(VERI_PORT) + " udp=" + String(VERI_PORT);
  s += " fmt=text mode=live fw=" + kodla(YAZILIM) + " hw=" + kodla(DONANIM);
  if (onay) s += String(" ack=") + onay;      // ad verme / kartı göster cevabı
  s += "\n";
  kesif.beginPacket(ip, port);
  kesif.print(s);
  kesif.endPacket();
}

void komutKarakter(char c, bool satirSonu) {
  if (c == '\r') return;
  if (c != '\n' && komut.length() < 80) komut += c;
  if (c == '\n' || satirSonu) {
    komut.trim();
    if (komut == "DURDUR" || komut == "STOP") gonder = false;
    else if (komut == "BASLA" || komut == "START") gonder = true;
    else if (komut == "SIFIRLA" || komut == "RESET") sira = 0;
    komut = "";
  }
}

// ------------------------------------------------------------------ keşif (UDP 5099)
void kesifIsle() {
  int n = kesif.parsePacket();
  if (n <= 0) return;
  char b[200];
  int k = kesif.read((uint8_t *)b, sizeof b - 1);
  if (k <= 0) return;
  b[k] = 0;
  String m(b);
  m.trim();
  IPAddress ip = kesif.remoteIP();
  uint16_t port = kesif.remotePort();
  if (m.startsWith("OSKILO?")) {
    duyur(ip, port);
  } else if (m.startsWith("OSKILO NAME 1 ") && alan(m, "id") == kimlik) {
    String yeni = alan(m, "name");
    if (yeni != "\x01" && yeni.length() <= 63) {
      // kalıcı hafızaya yaz (kart kapanınca da hatırlanır); yazılamazsa cevap verme
      bool tamam = (yeni == ad) || hafiza.putString("ad", yeni) == yeni.length();
      if (tamam) {
        ad = yeni;
        duyur(ip, port, "name");
      }
    }
  } else if (m.startsWith("OSKILO BLINK 1 ") && alan(m, "id") == kimlik) {
    yanipBitis = millis() + 3000;
    yanipSonraki = millis();
    duyur(ip, port, "blink");
  }
}

// ------------------------------------------------------------------ veri: TCP 5000
void tcpIsle() {
  if (sunucu.hasClient()) {               // yeni bağlantı eskisinin yerini alır
#if defined(ESP_ARDUINO_VERSION_MAJOR) && ESP_ARDUINO_VERSION_MAJOR >= 3
    WiFiClient yeni = sunucu.accept();
#else
    WiFiClient yeni = sunucu.available();
#endif
    if (istemci) istemci.stop();
    istemci = yeni;
    istemci.setNoDelay(true);
    komut = "";
    Serial.println("Oskilo TCP ile baglandi: " + istemci.remoteIP().toString());
  }
  if (istemci) {
    if (!istemci.connected()) {
      istemci.stop();
      return;
    }
    while (istemci.available()) komutKarakter((char)istemci.read(), false);
  }
}

// ------------------------------------------------------------------ veri: UDP 5000 abonelik
void udpIsle() {
  if (aboneAdres && millis() - aboneZaman > 5000) aboneAdres = 0;   // Oskilo 5 sn sessiz: dur
  int n = veri.parsePacket();
  if (n <= 0) return;
  char b[160];
  int k = veri.read((uint8_t *)b, sizeof b - 1);
  if (k < 0) k = 0;
  b[k] = 0;
  IPAddress ip = veri.remoteIP();
  uint16_t port = veri.remotePort();
  bool ayni = aboneAdres && ip == aboneIP && port == aboneAdres;
  if (strncmp(b, "OSKILO SUB ", 11) == 0) {
    if (!ayni) {
      aboneIP = ip;
      aboneAdres = port;
      komut = "";
      Serial.println("Oskilo UDP ile abone oldu: " + ip.toString());
    }
    aboneZaman = millis();
    veri.beginPacket(ip, port);
    veri.print("OSKILO SUBOK 1\n");
    veri.endPacket();
  } else if (strncmp(b, "OSKILO UNSUB ", 13) == 0) {
    if (ayni) aboneAdres = 0;
  } else if (ayni) {
    aboneZaman = millis();
    for (int i = 0; i < k; i++) komutKarakter(b[i], i == k - 1);
  }
}

bool bagli() { return (istemci && istemci.connected()) || aboneAdres; }

void gonderSatir(const char *s, size_t n) {
  if (istemci && istemci.connected()) istemci.write((const uint8_t *)s, n);
  if (aboneAdres) {
    veri.beginPacket(aboneIP, aboneAdres);
    veri.write((const uint8_t *)s, n);
    veri.endPacket();
  }
}

// ------------------------------------------------------------------ kurulum
void setup() {
  Serial.begin(115200);
  pinMode(LED_PIN, OUTPUT);
  analogReadResolution(12);

  uint64_t mac = ESP.getEfuseMac();
  uint32_t h = 2166136261UL;              // FNV-1a
  for (int i = 0; i < 6; i++) {
    h ^= (uint8_t)(mac >> (8 * i));
    h *= 16777619UL;
  }
  snprintf(kimlik, sizeof kimlik, "%08lX", (unsigned long)h);
  hafiza.begin("oskilo", false);
  ad = hafiza.getString("ad", "");

  if (strlen(WIFI_AG) > 0) {
    WiFi.mode(WIFI_STA);
    WiFi.setAutoReconnect(true);
    WiFi.begin(WIFI_AG, WIFI_SIFRE);
    Serial.print("Wi-Fi'a baglaniliyor");
    uint32_t t0 = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - t0 < 15000) {
      delay(250);
      Serial.print(".");
    }
    Serial.println();
  }
  if (WiFi.status() != WL_CONNECTED) {     // ağ yok ya da bağlanamadı: kendi ağını aç
    apModu = true;
    WiFi.mode(WIFI_AP);
    String ag = String("Oskilo-") + (kimlik + 4);
    WiFi.softAP(ag.c_str(), AP_SIFRE);
    Serial.println("Kendi Wi-Fi agi acildi: " + ag + "  sifre: " + AP_SIFRE);
  }
  WiFi.setSleep(false);                    // güç tasarrufu kapalı: gecikme ve kayıp azalır

  kesif.begin(KESIF_PORT);
  veri.begin(VERI_PORT);
  sunucu.begin();
  sunucu.setNoDelay(true);
  Serial.println("Oskilo karti hazir. Kimlik " + String(kimlik) + "  IP " + benimIP().toString());
  sonraki = millis();
}

// ------------------------------------------------------------------ ana döngü
void loop() {
  kesifIsle();
  tcpIsle();
  udpIsle();

  if (yanipBitis) {                        // "Kartı göster": LED 3 sn yanıp söner
    if ((int32_t)(millis() - yanipBitis) >= 0) {
      yanipBitis = 0;
      digitalWrite(LED_PIN, LOW);
    } else if ((int32_t)(millis() - yanipSonraki) >= 0) {
      yanipSonraki = millis() + 150;
      ledDurum = !ledDurum;
      digitalWrite(LED_PIN, ledDurum ? HIGH : LOW);
    }
  }

  if ((int32_t)(millis() - sonraki) >= 0) {
    sonraki += 20;                         // 50 Hz
    if (gonder && bagli()) {
      char s[48];
      int n = snprintf(s, sizeof s, "%lu,%d,%d\r\n", (unsigned long)sira, analogRead(ADC1_PIN), analogRead(ADC2_PIN));
      gonderSatir(s, (size_t)n);
      sira++;
    }
  }
}
