<p align="center">
  <img src="assets/icon.png" width="96" alt="Oskilo simgesi">
</p>

<h1 align="center">Oskilo</h1>

<p align="center">
  <a href="README.md">🇬🇧 English</a> · <b>🇹🇷 Türkçe</b>
</p>

<p align="center">
  <b>STM32, Arduino, ESP32</b> ve seri porttan veri gönderen her kart için kayıpsız veri kaydedici.<br>
  Canlı grafik, kayıp veri tespiti, FFT, eşik alarmları ve tek tıkla <b>Excel / CSV / TXT</b> çıktısı.<br>
  <sub>Eski adı: <i>Data Logger</i> (v2.x)</sub><br>
  <sub><b>IECIST</b></sub>
</p>

<p align="center">
  <a href="https://github.com/mhmtcndsgn/Oskilo/releases/latest/download/Oskilo_Windows.zip"><img src="https://img.shields.io/badge/%C4%B0ndir-Windows-0078D6?style=for-the-badge&logo=windows&logoColor=white" alt="Windows için indir"></a>
  <a href="https://github.com/mhmtcndsgn/Oskilo/releases/latest/download/Oskilo_macOS.dmg"><img src="https://img.shields.io/badge/%C4%B0ndir-macOS-000000?style=for-the-badge&logo=apple&logoColor=white" alt="macOS için indir"></a>
  <br>
  <a href="https://github.com/mhmtcndsgn/Oskilo/releases/latest"><img src="https://img.shields.io/github/v/release/mhmtcndsgn/Oskilo?label=son%20s%C3%BCr%C3%BCm&style=flat-square" alt="Son sürüm"></a>
  <img src="https://img.shields.io/badge/lisans-GPL--3.0-blue?style=flat-square" alt="GPL-3.0 lisansı">
</p>

![Oskilo](docs/ekran_deney.png)

---

## ✨ Özellikler

- **Sıfır veri kaybı.** Okuma ayrı bir thread'de çalışır, diske ya da ekrana hiç beklemez. Bu yüzden yüksek hızlı toplu gönderimler de (örneğin 921600 baud'da gönderilen bir DMA tamponu) eksiksiz gelir. 300.000 satırlık tek parça aktarımla, saniyede yaklaşık 125.000 satır hızında test edildi.
- **Kayıp tespiti.** Kart sıra numarası ve `START,N` / `END,N` gönderirse kaybolan her satır sayılır ve grafikte kırmızıyla gösterilir.
- **İkili (CRC) veri biçimi:** metne göre 2–4 kat az bayt; kabloda bozulan çerçeve CRC16 ile ayıklanıp sayılır, kayda girmez. STM32 ve Arduino için hazır gönderme kodu ([`oskilo_ikili.h`](firmware_ornek/oskilo_ikili.h)).
- **İki mod**
  - **Canlı izleme:** veriler geldikçe grafikte akar.
  - **Deney aktarımı:** kart deneyi belleğe kaydeder ve sonunda topluca gönderir. Uygulama aktarımın bittiğini anlar, otomatik kaydeder ve sonraki deneyi bekler.
- **Excel, CSV ya da TXT.** Excel dosyasında özet sayfası (bütünlük, min/max/ortalama), veriler ve grafik bulunur. Ayrıca gelen her baytın ham yedeği tutulur.
- **Hızlı canlı grafik:** zoom, kaydırma ve PNG olarak kaydetme. 1 milyon nokta yaklaşık 1 saniyede çizilir. Görünüm verinin dışına kaçmaz; sadece terminali okumak isterseniz grafik tek tıkla gizlenir.
- **Analiz araçları**
  - **📏 Ölçüm imleçleri:** iki dikey çizgi (A/B); aradaki süre (Δ), frekans (1/Δ) ve her kanalın değer farkı.
  - **📊 FFT:** grafikte görünen aralığın frekans spektrumu ve her kanalın tepe frekansı.
  - **Canlı istatistik:** görünen aralık için son / min / max / ortalama / RMS / std. sapma / tepe frekansı.
  - **Birim dönüşümü:** kanal başına çarpan, ofset ve birim (örn. ADC sayısı → Volt, tek tıkla ADC yardımcısı). Grafik, istatistik ve Excel/CSV çıktısı dönüştürülmüş değerleri kullanır.
- **📝 Notlar:** kayıt sırasında **Ctrl+M** ile o ana not düşün (örn. "motor açıldı"). Not grafikte işaretlenir, Excel/CSV'de *Not* sütununa yazılır ve **Notlar** sekmesinde listelenir.
- **🔔 Eşik alarmları:** kanal eşiği aşınca uyarı bandı, grafikte eşik çizgisi ve o satıra otomatik not; istenirse ses ve karta komut (örn. `STOP`). Geri dönüş payı sayesinde eşik etrafında titreyen sinyal alarm yağdırmaz.
- **⏺️ Tetikli kayıt:** başında olmadığınız testlerde sadece olayları kaydedin. Bir alarmı *Kaydı başlat* olarak işaretleyin; alarm tetiklenince tetikten önceki saniyeler (0–60 sn) ve sonraki belirlenen süre ayrı bir dosyaya (`ad_tetik_…`) kaydedilir, ardından bir sonraki olay beklenir. Sürekli akış ekranda görünür ama diske yazılmaz.
- **🔄 Uygulama içi güncelleme (Windows):** Yeni sürüm çıkınca üstte bir bant belirir. **Güncelle**'ye basınca yeni sürüm indirilir, `SHA256SUMS.txt` ve derlemenin dosya listesiyle doğrulanır, Oskilo yeni sürümle yeniden açılır. Yeni sürüm açılamazsa önceki sürüm otomatik geri gelir. macOS'ta yeni `.dmg` indirilip açılır.
- **🌐 Ağ (Ethernet / Wi-Fi), IP yazmadan:** Oskilo ağ protokolünü bilen kartlar kendiliğinden bulunur ve COM portlarının yanında `🌐 kartın adı` olarak listelenir: kabloyu takın, kartı seçin, bağlanın. Oskilo kartı kimliğiyle hatırlar (IP değişebilir), bağlantı koparsa kendisi yeniden bağlanır, karta ad verebilir (kartın hafızasına yazılır) ve LED'ini yakıp söndürebilir. Varsayılan TCP, çok yüksek hızlar için UDP. Hazır kart yazılımı: [STM32 + W5500](firmware_ornek/stm32_w5500/) ve [ESP32 Wi-Fi](firmware_ornek/esp32_wifi/esp32_wifi.ino); protokol: [`docs/ag_protokolu.md`](docs/ag_protokolu.md). Diğer kartlara IP yazarak (TCP) ya da port dinleyerek (UDP) bağlanılabilir. `araclar/ag_test_karti.bat` sahte bir kart çalıştırır.
- **Kart tanıma:** STM32 (ST-Link), Arduino, ESP32, CH340, CP2102, FTDI ve Raspberry Pi Pico portları ★ ile işaretlenir.
- **Karta komut gönderme** (örneğin yeni aktarım istemek için `GONDER`), yazı ya da **HEX bayt** olarak; **↑ / ↓** ile önceki komutlar geri gelir. **HEX görünüm** ham baytları gösterir.
- **Hızlı komut butonları:** sık gönderdiğiniz komutları buton yapın; istenirse belirli aralıkla tekrar gönderilir (örn. her 2 s'de `OKU`).
- **Otomatik yeniden bağlanma:** USB kablosu çıkarsa ya da kart yeniden başlarsa o ana kadarki veri kaydedilir, port geri gelince uygulama kendiliğinden bağlanır.
- **Kayıt yanlışlıkla kaybolmaz:** Kaydedilmemiş veri varken kapatırken / yeniden bağlanırken / dosya açarken sorar; dosyaları yarım kalmayacak şekilde yazar; çok uzun canlı kayıtları bellek dolmasın diye her 1 milyon satırda böler.
- **Uzun testlere hazır (günler, haftalar):** Canlı izlemede kayıt seçtiğiniz aralıkla (varsayılan 5 dakikada bir) aynı dosyaya yazılır, **Şimdi kaydet** hemen yazar, bağlıyken bilgisayar uykuya geçmez, disk dolmak üzereyse uyarı bandı çıkar.
- **Kısayollar:** F5 bağlan/kes · Ctrl+S şimdi kaydet · Ctrl+Shift+S farklı kaydet · Ctrl+O dosya aç · Ctrl+L terminali temizle · Ctrl+M not ekle · F1 kılavuz.
- **Renkli temalar:** Koyu, açık ya da Windows'a uyan. Varsayılan *Oskilo* (lacivert + turkuaz), *Nötr* gri ya da altı renk (mavi, pembe, mor, yeşil, turuncu, turkuaz); renk seçince tüm ekran, Windows 11'de başlık çubuğu ve uyarı pencereleri de o tonu alır.
- **Sade ekran:** soldaki kartlar başlığa tıklayınca katlanır, katlıyken kısa özet gösterir; *Bağlan* ve *Şimdi kaydet* düğmeleri görünür kalır.
- **Türkçe / İngilizce** arayüz, ayarlar hatırlanır (bir sorun olursa *? → Ayarları sıfırla*).
- **DEMO portu:** kart olmadan her şeyi deneyebilirsiniz.
- **Resimli kullanım kılavuzu** (Türkçe / İngilizce): **?** butonu ya da **F1**. Aynı menüde *Güncellemeleri kontrol et*, *Hata bildir / öneri* ve *Hakkında* da var.
- **İlk açılıştan sonra Windows uyarısı yok:** Uygulama bir kez sorar ve izin verirseniz Windows'un "internetten indirildi" etiketini kaldırır (*Özellikler → Engellemeyi kaldır* ile aynı işlem). Bu sadece parmak izi derlemeyle birebir aynı olan kendi dosyalarına uygulanır.
- **Dosyaları doğrula:** *? → Hakkında → Dosyaları doğrula*, uygulama klasöründeki her dosyanın SHA-256 parmak izini kontrol eder; değişen, eksik ya da sonradan eklenen (tanınmayan) dosyaları gösterir. Bkz. [Güvenlik](#-güvenlik).

![Kayıp tespiti](docs/ekran_kayip.png)

![Uygulama içi kılavuz](docs/guide_tr.png)

---

## ⬇️ Kurulum

### Windows 10 / 11 — kurulum programı (önerilen)
1. **[Oskilo_Setup.exe](https://github.com/mhmtcndsgn/Oskilo/releases/latest/download/Oskilo_Setup.exe)** dosyasını indirip çalıştırın → **İleri** → **Kur**.
   - Yönetici izni istemez: sadece sizin kullanıcınıza kurar (`%LOCALAPPDATA%\Programs\Oskilo`).
   - Başlat menüsüne eklenir (masaüstü kısayolu isteğe bağlı), **Ayarlar → Uygulamalar**'dan kaldırılır. Kaldırırken ayarlarınızın da silinip silinmeyeceği sorulur; kayıtlarınız (Belgeler\Oskilo) hiçbir zaman silinmez.
   - Uygulama içinden güncelleme (**? → Güncellemeleri denetle**) çalışmaya devam eder.
   - Kurulum programı henüz dijital olarak imzalı değil (bkz. [Kod imzalama](#-kod-imzalama-politikası)). Mavi "Windows bilgisayarınızı korudu" penceresi çıkarsa **Ek bilgi → Yine de çalıştır**.

### Windows — kurulumsuz (taşınabilir)
1. **[Oskilo_Windows.zip](https://github.com/mhmtcndsgn/Oskilo/releases/latest/download/Oskilo_Windows.zip)** dosyasını indirin.
2. **Zip'i açmadan önce** sağ tıklayın → **Özellikler** → alttaki **Engellemeyi kaldır** → **Tamam** (SmartScreen uyarısı çıkmaz; atlarsanız uygulama ilk açılışta bunu kendisi teklif eder).
3. Çıkarın ve **`Oskilo.exe`**'yi çalıştırın. Exe yanındaki `_internal` klasörüyle birlikte durmalı. USB bellek ve laboratuvar bilgisayarları için uygundur.

### En düşük sistem gereksinimi
| | En az | Önerilen |
|---|---|---|
| İşletim sistemi | Windows 10 64 bit / macOS 12 (Apple Silicon) | Windows 11 / macOS 14 |
| İşlemci / bellek | 2 çekirdek, 4 GB | 4 çekirdek, 8 GB |
| Ekran | 1366×768 (%100–150 ölçek) | 1920×1080 |
| Disk | Uygulama için 400 MB + kayıtlar için yer | SSD |

Yavaş bilgisayarda **🎨 → Hafif mod**'u açın: grafik daha seyrek ve yumuşatmasız çizilir. Kayıt hiç etkilenmez. 1 kHz × 4 kanal bir günlük kayıt CSV olarak yaklaşık 9 GB tutar; haftalarca sürecek testten önce diskteki boş yeri kontrol edin.

### macOS (Apple Silicon, M1 ve sonrası)
1. **[Oskilo_macOS.dmg](https://github.com/mhmtcndsgn/Oskilo/releases/latest/download/Oskilo_macOS.dmg)** dosyasını indirin, uygulamayı **Applications** klasörüne sürükleyin.
2. İlk açılış: uygulamayı bir kez açmayı deneyin, sonra **Sistem Ayarları → Gizlilik ve Güvenlik** sayfasında **Yine de Aç**'a basın (eski macOS'ta: uygulamaya sağ tık → **Aç**).

### İndirmeyi doğrulama (isteğe bağlı)
- Dosyanın SHA-256 değerini sürüm sayfasındaki `SHA256SUMS.txt` ile karşılaştırın: PowerShell'de `Get-FileHash Oskilo_Setup.exe`, macOS'ta `shasum -a 256 Oskilo_macOS.dmg`.
- Bu reponun GitHub Actions'ında derlendiğini kontrol edin: `gh attestation verify Oskilo_Windows.zip -R mhmtcndsgn/Oskilo`

---

## 🚀 Kullanım

1. Portu seçin (tanınan kartlar ★ ile işaretlidir) ve kart koduyla aynı baud hızını ayarlayın.
2. **Canlı izleme** ya da **Deney aktarımı** modunu seçip **Bağlan**'a basın.
3. Dosyalar varsayılan olarak `Belgeler\Oskilo` klasörüne kaydedilir. **Kayıtlı dosyayı aç…** ile eski kayıtlar (v1 dosyaları dahil) yeniden açılabilir.

---

## 📡 Veri biçimi (kart tarafı)

Uygulama her satırda şunların hepsini anlar:

```
1450                      tek değer
12,345,678                çok kanal (virgül, noktalı virgül, boşluk ya da tab)
Sicaklik:25.3 Nem:60      isimli kanallar
zaman,adc1,adc2           başlık satırı (veriden önce gelirse) → kanal adları
START,4000 / END,4000     aktarımın başı / sonu
```

**Kayıp tespiti için önerilen biçim:** ilk sütunda sıra numarası, başta `START,N`, sonda `END,N`:

```
START,4000
0,2048
1,2051
...
3999,2040
END,4000
```

Hazır örnekler:
- **STM32 (HAL):** [`firmware_ornek/veri_gonder.c`](firmware_ornek/veri_gonder.c): timer tetiklemeli ADC + DMA + UART gönderimi
- **Arduino / ESP32 / ESP8266:** [`firmware_ornek/arduino_ornek/arduino_ornek.ino`](firmware_ornek/arduino_ornek/arduino_ornek.ino): canlı mod ya da `GONDER` komutuyla deney aktarımı
- **Ağ, IP yazmadan:** [`firmware_ornek/stm32_w5500/`](firmware_ornek/stm32_w5500/) (STM32 + W5500 Ethernet, kütüphane + CubeMX rehberi) ve [`firmware_ornek/esp32_wifi/esp32_wifi.ino`](firmware_ornek/esp32_wifi/esp32_wifi.ino) (ESP32 Wi-Fi)
- **İkili (CRC) biçim, her kart:** [`firmware_ornek/oskilo_ikili.h`](firmware_ornek/oskilo_ikili.h) (tek dosya) ve Arduino örneği [`firmware_ornek/arduino_ikili/arduino_ikili.ino`](firmware_ornek/arduino_ikili/arduino_ikili.ino). Uygulamada *Bağlantı → Veri biçimi → İkili (CRC)* seçin.

  ```
  A5 5A | tip | kanal sayısı | sıra no (uint32) | değerler | CRC16 (CCITT-FALSE)
  tip: 0x01 uint16 · 0x02 int16 · 0x03 uint32 · 0x04 int32 · 0x05 float32 · 0xF0 START · 0xF1 END
  ```

> ⚠️ **STM32 kullananlar:** ADC'nin DMA'sı **Circular** modda çalışırken gönderim yaparsanız tampon başa dönüp eski verinin üzerine yazar. Bu da verilerin "kayması" gibi görünür. **Normal** modu kullanın ve göndermeden önce `HAL_ADC_Stop_DMA` çağırın.

---

## 🛠️ Kaynak koddan çalıştırma

```bash
pip install -r requirements.txt
python main.py
```
Windows'ta **`baslat.bat`** dosyasına çift tıklamanız da yeterli; ilk seferde gerekenleri kendisi kurar.

Testler: `pip install pytest && python -m pytest tests`

### Yeni sürüm yayınlamak
1. `stm32logger/__init__.py` içindeki `__version__` değerini ayarlayın (örn. `3.2.0`).
2. **Actions → Build Oskilo → Run workflow**'a tıklayın, sürüm kutusuna aynı sürümü başında `v` ile yazın (örn. `v3.2.0`) ve **Run**'a basın.
3. Test sürümü için ayrıca **prerelease** kutusunu işaretleyin: sürüm "Pre-release" olarak çıkar, "latest" olmaz ve sadece **? → Test sürümlerini de al** açık olan uygulamalara önerilir. Sorunsuzsa GitHub'da sürümü düzenleyip *pre-release* işaretini kaldırın ve en son sürüm yapın.

Windows ve macOS sürümleri otomatik test edilip (Windows'ta uygulama içi güncelleme de gerçek exe ile denenir: kapanışta, yeniden başlatarak, başka pencere açıkken, yarıda kalan güncelleme, açılamayan yeni sürüm) `SHA256SUMS.txt` ve derleme kanıtıyla birlikte **Releases**'a eklenir. Yukarıdaki indirme butonları her zaman en son sürümü indirir. İki sürüm numarası farklıysa iş akışı yayınlamayı reddeder.

---

## 🔒 Güvenlik

- Sürümler sadece bu reponun GitHub Actions'ında derlenir: sabitlenmiş action'lar, parmak izi kontrollü bağımlılıklar, sadece okuma yetkili token, SHA-256 özetleri ve imzalı derleme kanıtı.
- Uygulama yönetici izni istemez. Yeni sürümü ancak siz **Güncelle**'ye basınca, sadece bu reponun sürümlerinden ve SHA-256 ile doğrulayarak indirir.
- Karttan gelen veriye güvenilmez (uzunluk / kanal sınırı, formül çalıştıramayan CSV/Excel çıktısı).
- Bir güvenlik açığı bulursanız lütfen gizli bildirin: **[SECURITY.md](SECURITY.md)**.

---

## 🔏 Kod imzalama politikası

Windows sürümleri yalnızca bu repodaki herkese açık GitHub Actions iş akışıyla, bu repodaki kaynak koddan derlenir. [SignPath Foundation](https://signpath.org) üzerinden ücretsiz kod imzalama için başvuru yapıldı. Onaylanana kadar sürümler imzasızdır.

- **Geliştirici ve gözden geçiren:** [mhmtcndsgn](https://github.com/mhmtcndsgn)
- **Onaylayan:** [mhmtcndsgn](https://github.com/mhmtcndsgn)

### Gizlilik
Bu program verilerinizi ya da ölçümlerinizi ağ üzerinden başka bir sisteme göndermez. Yalnızca seçtiğiniz cihazla (seri port ya da yerel ağdaki bir kart) haberleşir ve seçtiğiniz klasöre dosya yazar. Kartları bulmak için yalnızca yerel ağa (internete değil) küçük "kim var?" paketleri gönderir (UDP port 5099). Tek ağ istekleri güncelleme içindir: açılışta günde en fazla bir kez (**? → Açılışta güncellemeleri denetle** ile kapatılabilir) ve **Güncellemeleri kontrol et**'e bastığınızda GitHub'dan son sürüm bilgisi okunur; yeni sürüm ancak siz onaylarsanız GitHub'dan indirilir. **Hata bildir** sadece tarayıcınızda bir GitHub sayfası açar; siz göndermedikçe hiçbir şey gönderilmez.

---

## 📄 Lisans

© 2026 Mehmet Can Dusgun ([mhmtcndsgn](https://github.com/mhmtcndsgn))

- **Oskilo uygulaması** [GNU Genel Kamu Lisansı v3.0 ya da sonrası (GPL-3.0-or-later)](LICENSE) ile özgür yazılımdır. Kullanabilir, inceleyebilir, paylaşabilir ve değiştirebilirsiniz. Kendisini ya da değiştirilmiş bir sürümünü dağıtırsanız, o sürüm de GPL ile ve kaynak koduyla birlikte paylaşılmalı, telif yazıları korunmalıdır.
- [`firmware_ornek/`](firmware_ornek/) klasöründeki **kart tarafı örnek kodlar** [MIT lisanslıdır](firmware_ornek/LICENSE): kendi kart yazılımınızda, kapalı kaynak projelerde bile serbestçe kullanabilirsiniz.
- **v3.3.0 dahil** önceki sürümler MIT lisansıyla yayınlandı ve o lisansla kalır.
- **Ticari lisans:** Oskilo'yu kapalı kaynak bir üründe kullanmak isterseniz ayrı bir lisans için yazarla iletişime geçin.

### Ad ve logo

**Oskilo** adı ve Oskilo logosu Mehmet Can Dusgun'a aittir ve GPL'in kapsamında değildir. Değiştirilmiş sürümler farklı bir ad ve logo kullanmalı, resmî Oskilo olduğu izlenimini vermemelidir. Değiştirilmemiş kopyalar adı koruyabilir.

### Kaynak gösterme

Oskilo tezinize ya da makalenize yardımcı olduysa lütfen kaynak gösterin. GitHub'daki **Cite this repository** düğmesi [`CITATION.cff`](CITATION.cff) dosyasını kullanır.
