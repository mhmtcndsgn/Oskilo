"""Uygulama içi kullanım kılavuzu (Türkçe / English).

Her konu: (başlık, kısa özet, gövde). Gövdede:
    {img:ad|açıklama}   -> assets/help/<dil>/<ad>.png görseli (2 kat çözünürlük, net görünsün diye)
    S(...) satırları     -> numaralı adımlar; numaralar görsellerdeki sarı rozetlerle aynıdır
Görselleri yeniden üretmek için:  python packaging/make_help_images.py
"""
from __future__ import annotations

import html as _html
import re

TOPICS = ["quickstart", "connect", "modes", "format", "saving", "longrun", "chart", "analysis", "look", "trouble",
          "security"]


def S(*rows) -> str:
    """Numaralı adımlar: (numara, başlık, açıklama)."""
    out = ['<table width="100%" cellspacing="0" cellpadding="0">']
    for num, title, text in rows:
        badge = (f'<table cellspacing="0" cellpadding="4"><tr><td class="num" width="22" align="center">{num}</td>'
                 f'</tr></table>')
        out.append(f'<tr><td width="40" valign="top" style="padding-top:8px">{badge}</td>'
                   f'<td style="padding:8px 0 8px 4px"><b>{title}</b><br>{text}</td></tr>')
    out.append("</table>")
    return "".join(out)


def TIP(icon: str, text: str) -> str:
    return f'<table width="100%" cellpadding="10"><tr><td class="tip">{icon}&nbsp; {text}</td></tr></table>'


def TABLE(head, rows) -> str:
    h = "".join(f"<th>{c}</th>" for c in head)
    b = "".join("<tr>" + "".join(f'<td class="row">{c}</td>' for c in r) + "</tr>" for r in rows)
    return f'<table width="100%" cellspacing="0">{"<tr>" + h + "</tr>"}{b}</table>'


GUIDE: dict[str, dict[str, tuple[str, str, str]]] = {
    # ================================================================== TÜRKÇE
    "tr": {
        "quickstart": ("Hızlı başlangıç", "Kart takılı olmasa da uygulamayı 30 saniyede deneyebilirsiniz.",
            "{img:quickstart_a|Bağlantı kartı}"
            + S((1, "Port", "Listeden <b>DEMO — kart olmadan dene</b>'yi seçin."),
                (2, "Çalışma modu", "<b>Deney aktarımı</b>'nı seçin."),
                (3, "Bağlan", "Sahte bir kart 4000 örneklik bir deney gönderir. Kısayol: <b>F5</b>."))
            + "{img:quickstart_b|Üst kutucuklar ve sonuç bandı}"
            + S((4, "Kutucuklar", "Satır sayısı, hız, <b>eksik veri</b>, süre ve durum burada görünür."),
                (5, "Sonuç bandı", "Aktarım bitince yeşil bant çıkar ve dosya kendiliğinden kaydedilir. "
                                   "<i>Dosyayı aç</i> ile Excel'de açabilirsiniz."))
            + TIP("💡", "Komut kutusuna <code>GONDER</code> yazıp <b>Gönder</b>'e basarsanız DEMO yeni bir deney gönderir. "
                        "Grafikte fare tekerleğiyle yakınlaştırıp sürükleyerek kaydırabilirsiniz.")),
        "connect": ("Kart bağlama", "Portu, baud hızını ve modu seçip bağlanın.",
            "{img:connect|Bağlantı ve veri ayarları}"
            + S((1, "Port", "Kartınızın portu. Tanınan kartlar (STM32, Arduino, ESP32, CH340, CP2102, FTDI, Pico) <b>★</b> ile işaretlenir ve başa gelir."),
                (2, "Yenile", "Kartı sonradan taktıysanız basın. Liste zaten 2 saniyede bir yenilenir."),
                (3, "Baud hızı", "Kart kodundaki değerle <b>aynı</b> olmalı (ör. 115200, 921600). Listede yoksa elle yazın."),
                (4, "Çalışma modu", "Canlı izleme ya da Deney aktarımı (sonraki konu)."),
                (5, "Veri biçimi", "<b>Metin</b>: kart sayıları yazı olarak gönderir. <b>İkili (CRC)</b>: daha hızlı ve bozuk veriye karşı korumalı (bkz. <i>Kart tarafı: veri biçimi</i>)."),
                (6, "USB koparsa yeniden bağlan", "Kablo çıkarsa ya da kart yeniden başlarsa o ana kadarki veri kaydedilir; port geri gelince otomatik bağlanılır."),
                (7, "Bağlan / Bağlantıyı kes", "Kısayol: <b>F5</b>. Kart katlıyken de görünür."),
                (8, "Örnekleme frekansı", "Kart sabit hızda örnekliyorsa (ör. 1000 Hz) yazın; Excel'de her örneğin zamanı hesaplanır. Bilmiyorsanız boş bırakın."),
                (9, "Sıra numarası", "<i>Otomatik algıla</i> çoğu zaman doğrudur. İlk sütun 0, 1, 2… diye artıyorsa kayıp satırlar yakalanır."),
                (10, "Aktarım bitiş süresi", "Deney aktarımında bu kadar süre veri gelmezse aktarım bitti sayılır. <b>1 s</b> iyi bir değerdir."),
                (11, "Kanallar ve birimler", "Ham değeri gerçek birime çevirir (ör. ADC → Volt). Ayrıntı: <i>Analiz</i> konusu."),
                (12, "Eşik alarmları", "Bir kanal eşiği aşınca uyarır, not düşer; isterseniz ses çalar ya da karta komut gönderir. Ayrıntı: <i>Analiz</i> konusu."))
            + TIP("⚠️", "<b>Port görünmüyorsa:</b> Arduino klonları ve ESP kartları için <b>CH340</b> ya da <b>CP2102</b> sürücüsünü kurun. "
                         "Kablonun sadece şarj kablosu olmadığından emin olun. Port başka bir programda (Arduino IDE seri monitörü, STM32CubeIDE) açıksa önce oradan kapatın.")),
        "modes": ("Canlı izleme ve deney aktarımı", "Kartınızın veriyi nasıl gönderdiğine göre mod seçin.",
            TABLE(["", "Canlı izleme", "Deney aktarımı"],
                  [["<b>Ne zaman?</b>", "Kart ölçtükçe sürekli gönderiyorsa", "Kart deneyi belleğe (ör. DMA) kaydedip <b>sonunda topluca</b> gönderiyorsa"],
                   ["<b>Grafik</b>", "Akan grafik: son 10 s / 30 s / 1 dk / 5 dk ya da tümü", "Aktarım boyunca dolar, bitince tamamı gösterilir"],
                   ["<b>Kayıt</b>", "Seçtiğiniz aralıkla ara kayıt + bağlantı kesilince", "Her aktarım bitince; uygulama sonraki deneyi bekler"]])
            + "{img:modes|Aktarım sürerken}"
            + S((1, "Beklenen satır", "Kart <code>START,N</code> gönderirse kaç satır geleceği görünür."),
                (2, "İlerleme", "Aktarım sırasında çubuk dolar."),
                (3, "Durum", "<i>Veri bekleniyor</i> → <i>Alınıyor…</i> → <i>Tamamlandı</i>."))
            + TIP("💡", "Çok hızlı aktarımda terminal bazı satırları <b>göstermeyebilir</b>. Bu sadece ekranla ilgilidir; dosyaya <b>her satır</b> kaydedilir.")),
        "format": ("Kart tarafı: veri biçimi", "Kart her ölçümü bir satır olarak göndermeli.",
            "<p>Satır sonu <code>\\r\\n</code> ya da <code>\\n</code> olabilir. Uygulama şunların hepsini anlar:</p>"
            "<pre>1450                    tek değer\n12,345,678              çok kanal (virgül ; boşluk ya da tab)\n"
            "Sicaklik:25.3 Nem:60    isimli kanallar\nzaman,adc1,adc2         başlık (veriden önce) → kanal adları</pre>"
            "<h3>Kayıpları yakalamak için önerilen biçim</h3>"
            "<pre>START,4000      ← kaç satır geleceği\n0,2048          ← sıra no, değer(ler)\n1,2051\n...\n"
            "3999,2040\nEND,4000        ← aktarım bitti</pre>"
            "<p>İlk sütundaki sıra numarası sayesinde tek bir satır bile kaybolsa hangi aralıkta kaybolduğu gösterilir.</p>"
            "<h3>Arduino / ESP32</h3>"
            "<pre>Serial.print(\"START,\"); Serial.println(N);\nfor (int i = 0; i &lt; N; i++) {\n"
            "  Serial.print(i); Serial.print(','); Serial.println(buf[i]);\n}\nSerial.print(\"END,\"); Serial.println(N);</pre>"
            "<h3>STM32 (HAL)</h3>"
            "<pre>n = snprintf(s, sizeof s, \"%lu,%u\\r\\n\", (unsigned long)i, adc_buf[i]);\n"
            "HAL_UART_Transmit(&amp;huart2, (uint8_t*)s, n, HAL_MAX_DELAY);</pre>"
            + "<h3>İkili (CRC) biçim — hızlı ve korumalı</h3>"
            "<p><b>Bağlantı → Veri biçimi → İkili (CRC)</b>. Her örnek küçük bir çerçeve olarak gider; metne göre 2–4 kat az bayt tutar, "
            "kabloda bozulan çerçeve CRC sayesinde atılır ve sayılır, sıra numarası her çerçevede olduğu için kayıplar tek tek yakalanır.</p>"
            "<pre>A5 5A | tip | kanal sayısı | sıra no (4 bayt) | değerler | CRC16\n"
            "tip: uint16, int16, uint32, int32, float32 · START / END çerçeveleri de var</pre>"
            "<pre>#include \"oskilo_ikili.h\"\n"
            "static void yaz(const uint8_t *p, uint16_t n) { HAL_UART_Transmit(&amp;huart2, (uint8_t*)p, n, 100); }\n"
            "osk_gonder_u16(yaz, sira++, adc, 2);   // 2 kanal</pre>"
            + TIP("💡", "Hazır örnek kodlar GitHub sayfasındaki <code>firmware_ornek</code> klasöründe: metin için <code>veri_gonder.c</code> ve "
                        "<code>arduino_ornek</code>, ikili için <code>oskilo_ikili.h</code> ve <code>arduino_ikili</code>. Ondalıklı sayı yerine değeri 100 ile çarpıp tamsayı da gönderebilirsiniz (25,31 °C → 2531).")),
        "saving": ("Kayıt ve dosyalar", "Kayıtlar otomatik olarak seçtiğiniz klasöre yazılır.",
            "{img:saving|Kayıt kartı}"
            + S((1, "Dosya biçimi", "<b>Excel</b>: özet, veriler ve grafik sayfaları. <b>CSV</b>: Python/MATLAB için, uzun testlerde en hızlısı. <b>TXT</b>: sekmeyle ayrılmış sade metin."),
                (2, "Kayıt klasörü", "Varsayılan <code>Belgeler\\Oskilo</code>."),
                (3, "Dosya adı", "Dosyalar <code>ad_2026-10-17_14-30-05.xlsx</code> gibi adlandırılır."),
                (4, "Bitince otomatik kaydet", "Her aktarım ya da kayıt kendiliğinden kaydedilir."),
                (5, "Canlı izlemede ara kayıt", "Canlı kayıt bu aralıkla (ör. 5 dk) <b>aynı dosyaya</b> yazılır. Bilgisayar kapanırsa en fazla bu kadar süre kaybolur."),
                (6, "Ham yedek", "Karttan gelen her bayt hiç değiştirilmeden <code>ham</code> klasörüne de yazılır. Hep açık tutun."),
                (7, "Klasörü aç", "Kayıt klasörünü Dosya Gezgini'nde açar."),
                (8, "Kayıtlı dosyayı aç", "Eski kayıtları (v1 dosyaları ve ikili ham yedekler dahil) grafikte açar. Kısayol: <b>Ctrl+O</b>."),
                (9, "Şimdi kaydet", "Ekrandaki kaydı hemen dosyasına yazar. Kısayol: <b>Ctrl+S</b>. Kart katlıyken de görünür."),
                (10, "Farklı kaydet", "İstediğiniz ad ve biçimde ayrı bir kopya. Kısayol: <b>Ctrl+Shift+S</b>. Sonradan eklenen notlar da bu dosyaya yazılır."))
            + TIP("🛟", "Kaydedilmemiş veri varken pencereyi kapatırsanız, yeniden bağlanırsanız ya da dosya açarsanız uygulama sorar. "
                        "Dosyalar önce geçici adla yazılır, bitince asıl adını alır; yarım dosya oluşmaz.")
            + "<h3>Tetikli kayıt (sadece olayları kaydet)</h3>"
            "<p>Uzun süre başında olmadığınız testlerde her şeyi değil, sadece bir şey olduğunda kaydetmek için. Canlı modda çalışır.</p>"
            + S((1, "Alarmı işaretleyin", "<b>Veri ayarları → Eşik alarmları…</b> penceresinde kaydı başlatacak alarmların <b>Kaydı başlat</b> kutusunu işaretleyin."),
                (2, "Tetikli kaydı açın", "<b>Kayıt</b> kartında <b>Tetikli kayıt (alarmla başlat)</b>. <b>Önce</b>: tetikten önceki kaç saniye de kayda girsin (0–60, varsayılan 5). "
                    "<b>Süre</b>: tetikten sonra kaç saniye kaydedilsin (varsayılan 60)."),
                (3, "Bağlanın", "Durum çubuğunda <i>Tetik bekleniyor</i> yazar. Sürekli akış ekranda görünür ama dosyaya yazılmaz."),
                (4, "Olay olunca", "Kayıt başlar, durum çubuğunda süre sayılır. Süre dolunca olay <code>ad_tetik_2026-…</code> adıyla ayrı bir dosyaya kaydedilir; "
                    "tetik anı dosyada <b>▶ Tetik</b> notuyla işaretlidir. Sonra bir sonraki olay beklenir."))
            + TIP("⏱️", "Kayıt sürerken gelen yeni alarm yeni kayıt başlatmaz, mevcut kayda not olarak düşer. Bağlantıyı keserseniz süren kayıt o ana kadarki haliyle kaydedilir. "
                        "Ekrandaki sürekli akışı yine de saklamak isterseniz <b>Şimdi kaydet</b>'e basın.")),
        "longrun": ("Uzun testler (günler, haftalar)", "Bilgisayarı günlerce kayıtta bırakacaksanız bu sayfayı okuyun.",
            "<h3>Uygulama kendiliğinden ne yapar?</h3>"
            + S(("✓", "Ara kayıt", "Canlı izlemede kayıt seçtiğiniz aralıkla (varsayılan 5 dk) aynı dosyaya yazılır."),
                ("✓", "Bölme", "Kayıt 1 milyon satıra ulaşınca bellek dolmasın diye yeni dosyaya geçilir; eski kısım kaydedilir."),
                ("✓", "Uyku engeli", "Bağlıyken bilgisayar uykuya geçmez (ekran kapanabilir)."),
                ("✓", "Ham yedek", "Gelen her bayt yarım saniyede bir diske yazılır; 256 MB'ta yeni parçaya geçilir."),
                ("✓", "USB koparsa", "O ana kadarki veri kaydedilir, kart geri gelince otomatik bağlanılır."),
                ("✓", "Disk takibi", "Boş yer 500 MB'ın altına inerse ekranda uyarı bandı çıkar."))
            + "<h3>Testi bırakmadan önce Windows'ta yapın</h3>"
            + S((1, "Güncellemeleri duraklatın", "<b>Ayarlar → Windows Update → Güncellemeleri duraklat</b>. Yoksa Windows gece kendiliğinden yeniden başlayabilir."),
                (2, "Uykuyu kapatın", "<b>Ayarlar → Sistem → Güç</b>: uyku <b>Hiçbir zaman</b>. Dizüstüde kapak kapatınca <b>Hiçbir şey yapma</b>."),
                (3, "USB askıya almayı kapatın", "<b>Denetim Masası → Güç Seçenekleri → Plan ayarları → Gelişmiş</b> → <i>USB seçmeli askıya alma</i>: <b>Devre dışı</b>."),
                (4, "Biçim olarak CSV seçin", "Büyük kayıtlarda Excel'den çok daha hızlı yazılır ve daha az yer kaplar."))
            + TIP("💾", "Disk ihtiyacı kabaca: <b>satır uzunluğu × saniyedeki satır × süre × 2</b> (ham yedek + kayıt dosyası). "
                        "Örnek: 20 baytlık satır, 100 Hz, 3 hafta ≈ 7 GB.")),
        "chart": ("Grafik ve terminal", "Gelen veriyi grafikte ve ham haliyle terminalde görün.",
            "{img:chart|Grafik ve terminal}"
            + S((1, "X ekseni", "<i>Otomatik</i>, örnek no, zaman (s) ya da PC süresi."),
                (2, "Pencere", "Canlı izlemede son kaç saniyenin gösterileceği."),
                (3, "Sığdır", "Yakınlaştırdıktan sonra tüm veriyi tekrar gösterir."),
                (4, "PNG kaydet", "Grafiği rapor için resim olarak kaydeder."),
                (5, "Gösterge", "Bir kanalın adına tıklayınca o kanal gizlenir / gösterilir."),
                (6, "HEX / Duraklat / Temizle", "<b>HEX</b> gelen baytları onaltılık gösterir (baud yanlışsa ya da kart ikili veri gönderiyorsa işe yarar). Temizle: <b>Ctrl+L</b>. Kayıt etkilenmez."),
                (7, "Hızlı komut butonları", "Sık gönderdiğiniz komutlar tek tıkla gider. <b>＋</b> ile ekleyin, sağ tıkla düzenleyin / silin. Tekrar aralığı verirseniz buton basılı kaldıkça komut her X saniyede bir gönderilir."),
                (8, "Komut gönder", "Karta yazı gönderir (ör. <code>GONDER</code>). <b>HEX</b> işaretliyse <code>01 A0 FF</code> gibi bayt gönderir. <b>↑ / ↓</b> önceki komutları getirir."),
                (9, "Grafiği gizle / göster", "<b>▾ Grafik</b>'e tıklayınca grafik küçülür, yeri terminale kalır; sadece terminalden okumak için. Gizliyken grafik hiç çizilmez, bilgisayar rahatlar; kayıt aynen sürer."))
            + TIP("🖱️", "Tekerlek: yakınlaştır · Sol tık sürükle: kaydır · Sağ tık: eksen seçenekleri. Grafik verinin dışına kaçmaz: "
                        "en fazla tüm veri sığana kadar uzaklaşır, sürükleyince verinin biraz ötesinde durur.")),
        "analysis": ("Analiz: FFT, imleç, istatistik, notlar", "Kaydı uygulamadan çıkmadan inceleyin.",
            "{img:analysis|Analiz araçları}"
            + S((1, "📏 İmleç", "Grafiğe A ve B çizgileri koyar. Fareyle sürükleyin."),
                (2, "Ölçüm", "A ve B'deki değerler, aradaki fark (Δ) ve <b>1/Δ</b> frekansı. Bir periyodun başına ve sonuna koyarsanız frekansı okursunuz."),
                (3, "📊 FFT", "Grafikte görünen aralığın frekans içeriği. Titreşim, balans, motor gibi ölçümlerde hangi frekansın baskın olduğunu gösterir."),
                (4, "FFT grafiği", "Doğru Hz için <b>örnekleme frekansını</b> girin. Yakınlaştırırsanız sadece o aralığın FFT'si alınır."),
                (5, "📝 Not ekle", "Kayıt sırasında o ana not düşer (Ctrl+M). Grafikte sarı çizgiyle, Excel'de <b>Not</b> sütununda görünür; <b>Notlar</b> sekmesinde listelenir."),
                (6, "Not çizgisi", "Notun eklendiği an."),
                (7, "İstatistik", "Her kanal için son değer, min, max, ortalama, <b>RMS</b>, standart sapma ve FFT açıksa tepe frekansı — grafikte görünen aralık üzerinden."))
            + "<h3>Kanallar ve birimler</h3>"
            "<p><b>Veri ayarları → Kanallar ve birimler…</b> ile ham değeri gerçek birime çevirin: "
            "<b>gerçek değer = ham × çarpan + ofset</b>. <b>ADC → Volt…</b> butonu referans gerilimi ve bit sayısından çarpanı hesaplar "
            "(12 bit, 3,3 V → 0,000806). Grafik, istatistik ve kaydedilen dosyalar dönüştürülmüş değerleri kullanır; sütun adı "
            "<code>Kanal 1 [V]</code> olur. Ham yedek hiç değişmez.</p>"
            + TIP("💡", "Örnek: LM35 sıcaklık sensörü 10 mV/°C verir. 12 bit ADC, 3,3 V ile çarpan = 0,000806 × 100 = 0,0806, birim °C.")
            + "<h3>Eşik alarmları</h3>"
            "{img:alarms_dlg|Eşik alarmları penceresi}"
            "<p><b>Veri ayarları → Eşik alarmları…</b>: kanal, koşul (üstüne çıkarsa / altına inerse) ve eşik girin. "
            "Eşik, birim dönüşümü yapılmış değerle karşılaştırılır (ör. 3,0 V).</p>"
            "{img:alarms|Alarm çalınca}"
            + S((1, "Uyarı bandı", "Hangi kanalın hangi değerle eşiği aştığı ve saati."),
                (2, "Eşik çizgisi", "Grafikte kırmızı kesikli çizgi."),
                (3, "Otomatik not", "Alarmın olduğu satıra not düşülür: grafikte sarı çizgi, Excel'de <b>Not</b> sütunu."))
            + TIP("🔔", "<b>Geri dönüş payı</b>: değer eşiğin bu kadar gerisine inmeden alarm yeniden kurulmaz; eşik etrafında titreyen sinyal alarm yağdırmaz. "
                        "<b>Ses</b> ve <b>karta komut</b> (ör. <code>STOP</code>) isteğe bağlıdır; aynı alarm saniyede en fazla bir kez not, ses ve komut üretir. "
                        "<b>Kaydı başlat</b>: alarm tetiklenince olayı ayrı bir dosyaya kaydeder (bkz. <i>Kayıt ve dosyalar → Tetikli kayıt</i>).")),
        "look": ("Tema ve kısayollar", "Uygulamayı istediğiniz renklerde kullanın.",
            "<p>Sağ üstteki renkli yuvarlak butondan seçin:</p>"
            + S(("🌗", "Görünüm", "<b>Koyu</b>, <b>Açık</b> ya da <b>Sistem</b> (Windows'un koyu/açık ayarına uyar)."),
                ("🎨", "Renk", "<b>Oskilo</b> (logonun laciverti ve turkuazı, varsayılan), <b>Nötr</b> (klasik gri), mavi, pembe, mor, yeşil, turuncu ya da turkuaz. Renk seçince bütün ekran — zemin, kartlar, grafik, terminal — o rengin tonlarına bürünür. "
                        "Windows 11'de pencerenin başlık çubuğu ve uyarı pencereleri de aynı renge boyanır (Windows 10'da koyu/açık)."),
                ("▾", "Daha sade ekran", "Soldaki kartların (Bağlantı, Veri ayarları, Kayıt) başlığına tıklayınca katlanır; katlıyken yanında kısa bir özet görünür. "
                        "Grafik de <b>▾ Grafik</b> ile gizlenebilir. Uygulama hangilerinin açık olduğunu hatırlar."))
            + "<h3>Kısayollar</h3>"
            + TABLE(["Tuş", "İş"],
                    [["<b>F5</b>", "Bağlan / bağlantıyı kes"], ["<b>Ctrl+S</b>", "Şimdi kaydet"],
                     ["<b>Ctrl+Shift+S</b>", "Farklı kaydet"], ["<b>Ctrl+O</b>", "Kayıtlı dosyayı aç"],
                     ["<b>Ctrl+L</b>", "Terminali temizle"], ["<b>Ctrl+M</b>", "Not ekle"], ["<b>↑ / ↓</b>", "Önceki komutlar"],
                     ["<b>F1</b>", "Bu kılavuz"]])),
        "trouble": ("Sorun giderme", "Kırmızı bant ve kırmızı eksik sayısı bazı satırların gelmediğini gösterir.",
            "{img:trouble|Kayıplı bir aktarım}"
            + S((1, "Kırmızı bant", "Kaç örneğin eksik olduğunu söyler."),
                (2, "Eksik veri", "Kayıp örnek sayısı ve kaç yerde atlama olduğu."),
                (3, "Kayıp aralık", "Grafikte kırmızıyla işaretlenir; araya sahte değer konmaz."))
            + TABLE(["Belirti", "Muhtemel neden ve çözüm"],
                    [["Anlamsız karakterler, hiç sayı yok", "<b>Baud hızı</b> kart koduyla aynı değil."],
                     ["Veriler belli bir yerden sonra \"kayıyor\"", "STM32'de ADC DMA'sı <b>Circular</b> modda. <b>Normal</b> moda alın ve göndermeden önce <code>HAL_ADC_Stop_DMA</code> çağırın."],
                     ["Eksik veri \"kontrol edilemedi\"", "Kart sıra numarası ya da <code>START,N</code> göndermiyor (<i>Kart tarafı: veri biçimi</i>)."],
                     ["Aktarım iki dosyaya bölünüyor", "<b>Aktarım bitiş süresi</b> çok kısa; 1–2 s yapın."],
                     ["Port açılamadı", "Port başka programda açık (seri monitör, CubeIDE) ya da kart çıkarıldı."],
                     ["\"Bağlantı koptu, yeniden bağlanıyor…\"", "Gevşek kablo ya da kart yeniden başladı. Veri kaydedildi; sürekli oluyorsa başka kablo / USB girişi deneyin."],
                     ["Uygulama tuhaf davranıyor", "<b>? → Ayarları sıfırla</b> (açılmıyorsa <code>Oskilo.exe --reset-settings</code>)."]])
            + "<p>Çözemediğiniz bir sorun olursa <b>? → Hata bildir / öneri</b> ile yazın.</p>"),
        "security": ("Güvenlik ve Windows uyarısı", "Uygulama yönetici izni istemez, sistem ayarlarına dokunmaz.",
            "<h3>Dosyaların sağlam olduğunu kontrol etmek</h3>"
            + S(("✓", "Herkese açık derleme", "Uygulama sadece GitHub'daki açık derleme sisteminde, bu repodaki koddan üretilir; her sürümde derleme kanıtı vardır."),
                ("✓", "Dosyaları doğrula", "<b>? → Hakkında → Dosyaları doğrula</b>: klasördeki her dosyanın parmak izi (SHA-256) derlemeyle karşılaştırılır; değişen, eksik ya da sonradan eklenen dosyalar gösterilir."),
                ("✓", "İndirmeyi doğrula", "En güçlü kontrol: zip'in parmak izi sürüm sayfasındaki <code>SHA256SUMS.txt</code> ile aynı olmalı (PowerShell: <code>Get-FileHash Oskilo_Windows.zip</code>)."))
            + "<h3>\"Bu dosyayı çalıştırmak istiyor musunuz?\" uyarısı</h3>"
            "<p>Windows bu uyarıyı internetten indirilen ve <b>dijital imzası olmayan</b> programlarda gösterir. Oskilo açık kaynaklıdır; uyarı sadece henüz imza sertifikası olmamasından kaynaklanır.</p>"
            + S(("1", "Mavi ekran", "<b>Ek bilgi → Yine de çalıştır</b>. Windows bu kararı hatırlar."),
                ("2", "İlk açılış", "Uygulama durumu söyler: uyarı kalktıysa bilgi verir; kalkmadıysa sizin izninizle etiketi <b>sadece kendi, değişmemiş dosyalarından</b> kaldırır."),
                ("3", "Başkasına gönderirken", "Zip'i açmadan önce sağ tık → <b>Özellikler</b> → <b>Engellemeyi kaldır</b> → Tamam."))
            + "<h3>macOS</h3><p><b>Sistem Ayarları → Gizlilik ve Güvenlik → Yine de Aç</b> (sadece ilk açılışta). macOS sürümü Apple Silicon (M1 ve sonrası) içindir.</p>"
            "<h3>Gizlilik</h3><p>Hiçbir veri internete gönderilmez. Tek istisna, <b>Güncellemeleri kontrol et</b>'e bastığınızda GitHub'dan son sürüm numarasının okunmasıdır.</p>"),
    },
    # ================================================================== ENGLISH
    "en": {
        "quickstart": ("Quick start", "You can try the app in 30 seconds, even without a board.",
            "{img:quickstart_a|Connection card}"
            + S((1, "Port", "Choose <b>DEMO — try without a board</b>."),
                (2, "Mode", "Choose <b>Experiment dump</b>."),
                (3, "Connect", "A simulated board sends a 4000-sample experiment. Shortcut: <b>F5</b>."))
            + "{img:quickstart_b|Tiles and result banner}"
            + S((4, "Tiles", "Rows, rate, <b>missing data</b>, duration and status."),
                (5, "Result banner", "When the transfer ends a green banner appears and the file is saved automatically. Use <i>Open file</i> to open it in Excel."))
            + TIP("💡", "Type <code>SEND</code> in the command box and click <b>Send</b> to make the DEMO send a new experiment. "
                        "Zoom the chart with the mouse wheel and drag to pan.")),
        "connect": ("Connecting a board", "Choose the port, baud rate and mode, then connect.",
            "{img:connect|Connection and data settings}"
            + S((1, "Port", "Your board's port. Known boards (STM32, Arduino, ESP32, CH340, CP2102, FTDI, Pico) are marked <b>★</b> and listed first."),
                (2, "Refresh", "Use it if you plugged the board in later. The list also refreshes every 2 seconds."),
                (3, "Baud rate", "Must be the <b>same</b> as in your firmware (e.g. 115200, 921600). You can type a custom value."),
                (4, "Mode", "Live monitor or Experiment dump (next topic)."),
                (5, "Data format", "<b>Text</b>: the board sends numbers as text. <b>Binary (CRC)</b>: faster and protected against corrupted data (see <i>Board side: data format</i>)."),
                (6, "Reconnect if USB drops", "If the cable is unplugged or the board restarts, the data so far is saved and the app reconnects when the port is back."),
                (7, "Connect / Disconnect", "Shortcut: <b>F5</b>. Stays visible when the card is collapsed."),
                (8, "Sample rate", "If your board samples at a fixed rate (e.g. 1000 Hz), enter it; Excel then gets a time column. Leave it empty if unknown."),
                (9, "Sequence number", "<i>Auto-detect</i> is usually right. If the first column counts 0, 1, 2…, lost lines are detected."),
                (10, "Transfer end timeout", "In experiment dump mode the transfer counts as finished after this much silence. <b>1 s</b> is a good value."),
                (11, "Channels and units", "Converts raw values to real units (e.g. ADC → Volt). Details: <i>Analysis</i> topic."),
                (12, "Threshold alarms", "Warns and adds a note when a channel crosses a threshold; optionally plays a sound or sends a command to the board. Details: <i>Analysis</i> topic."))
            + TIP("⚠️", "<b>Port not listed?</b> Install the <b>CH340</b> or <b>CP2102</b> driver for Arduino clones and ESP boards. "
                         "Make sure the cable is not charge-only, and close the port in other programs (Arduino IDE serial monitor, STM32CubeIDE).")),
        "modes": ("Live vs. experiment mode", "Pick the mode that matches how your board sends data.",
            TABLE(["", "Live monitor", "Experiment dump"],
                  [["<b>When?</b>", "The board sends data continuously while measuring", "The board stores the experiment (e.g. DMA) and sends it <b>all at the end</b>"],
                   ["<b>Chart</b>", "Scrolling: last 10 s / 30 s / 1 min / 5 min or all", "Fills during the transfer, then shows everything"],
                   ["<b>Saving</b>", "Interim saves at your interval + when you disconnect", "After each transfer; then waits for the next one"]])
            + "{img:modes|During a transfer}"
            + S((1, "Expected rows", "Shown when the board sends <code>START,N</code>."),
                (2, "Progress", "The bar fills during the transfer."),
                (3, "Status", "<i>Waiting for data</i> → <i>Receiving…</i> → <i>Complete</i>."))
            + TIP("💡", "During very fast transfers the terminal may <b>skip displaying</b> some lines. Only the screen is affected; <b>every line</b> is saved.")),
        "format": ("Board side: data format", "Your board should send each measurement as one line.",
            "<p>Lines may end with <code>\\r\\n</code> or <code>\\n</code>. The app understands all of these:</p>"
            "<pre>1450                    single value\n12,345,678              multiple channels (comma ; space or tab)\n"
            "Temp:25.3 Hum:60        named channels\ntime,adc1,adc2          header (before data) → channel names</pre>"
            "<h3>Recommended format for loss detection</h3>"
            "<pre>START,4000      ← how many lines will follow\n0,2048          ← sequence number, value(s)\n1,2051\n...\n"
            "3999,2040\nEND,4000        ← transfer finished</pre>"
            "<p>With a sequence number in the first column, even a single lost line is detected and its position is shown.</p>"
            "<h3>Arduino / ESP32</h3>"
            "<pre>Serial.print(\"START,\"); Serial.println(N);\nfor (int i = 0; i &lt; N; i++) {\n"
            "  Serial.print(i); Serial.print(','); Serial.println(buf[i]);\n}\nSerial.print(\"END,\"); Serial.println(N);</pre>"
            "<h3>STM32 (HAL)</h3>"
            "<pre>n = snprintf(s, sizeof s, \"%lu,%u\\r\\n\", (unsigned long)i, adc_buf[i]);\n"
            "HAL_UART_Transmit(&amp;huart2, (uint8_t*)s, n, HAL_MAX_DELAY);</pre>"
            + "<h3>Binary (CRC) format — fast and protected</h3>"
            "<p><b>Connection → Data format → Binary (CRC)</b>. Each sample travels as a small frame: 2–4× fewer bytes than text, "
            "a frame corrupted on the cable is dropped and counted thanks to its CRC, and every frame carries a sequence number so losses are caught one by one.</p>"
            "<pre>A5 5A | type | channel count | sequence (4 bytes) | values | CRC16\n"
            "type: uint16, int16, uint32, int32, float32 · plus START / END frames</pre>"
            "<pre>#include \"oskilo_ikili.h\"\n"
            "static void put(const uint8_t *p, uint16_t n) { HAL_UART_Transmit(&amp;huart2, (uint8_t*)p, n, 100); }\n"
            "osk_gonder_u16(put, seq++, adc, 2);   // 2 channels</pre>"
            + TIP("💡", "Complete examples are in the <code>firmware_ornek</code> folder on GitHub: <code>veri_gonder.c</code> and "
                        "<code>arduino_ornek</code> for text, <code>oskilo_ikili.h</code> and <code>arduino_ikili</code> for binary. Instead of decimals you can multiply by 100 and send an integer (25.31 °C → 2531).")),
        "saving": ("Saving and files", "Recordings are written to your folder automatically.",
            "{img:saving|Saving card}"
            + S((1, "File format", "<b>Excel</b>: summary, data and chart sheets. <b>CSV</b>: for Python/MATLAB, fastest for long tests. <b>TXT</b>: plain tab-separated text."),
                (2, "Output folder", "<code>Documents\\Oskilo</code> by default."),
                (3, "File name", "Files are named like <code>name_2026-10-17_14-30-05.xlsx</code>."),
                (4, "Save automatically when done", "Every transfer or recording is saved on its own."),
                (5, "Interim save in live mode", "The live recording is written to the <b>same file</b> at this interval (e.g. 5 min). If the computer shuts down, at most this much is lost."),
                (6, "Raw backup", "Every byte from the board is also written, untouched, to the <code>ham</code> folder. Keep it on."),
                (7, "Open folder", "Opens the output folder."),
                (8, "Open saved file", "Reopens earlier recordings (including v1 files and binary raw backups) in the chart. Shortcut: <b>Ctrl+O</b>."),
                (9, "Save now", "Writes the recording on screen to its file right away. Shortcut: <b>Ctrl+S</b>. Stays visible when the card is collapsed."),
                (10, "Save as", "A separate copy with any name and format. Shortcut: <b>Ctrl+Shift+S</b>. Notes added later also go to this file."))
            + TIP("🛟", "If a recording is not saved yet, the app asks before you close the window, connect again or open a file. "
                        "Files are written under a temporary name first and renamed when complete, so no half-written files.")
            + "<h3>Triggered recording (save only the events)</h3>"
            "<p>For tests you leave unattended: instead of everything, save only when something happens. Works in live mode.</p>"
            + S((1, "Mark the alarm", "In <b>Data settings → Threshold alarms…</b> tick <b>Start rec.</b> for the alarms that should start a recording."),
                (2, "Turn on triggered recording", "In the <b>Save</b> card: <b>Triggered recording (start on alarm)</b>. <b>Before</b>: how many seconds before the trigger are included (0–60, default 5). "
                    "<b>Length</b>: how many seconds are recorded after the trigger (default 60)."),
                (3, "Connect", "The status bar shows <i>Waiting for trigger</i>. The continuous stream is shown on screen but not written to a file."),
                (4, "When an event happens", "Recording starts and the status bar counts the seconds. When the time is up the event is saved to its own file named <code>name_trigger_2026-…</code>; "
                    "the trigger moment is marked with a <b>▶ Trigger</b> note. Then the next event is awaited."))
            + TIP("⏱️", "An alarm during a recording does not start a new one; it is added to the current recording as a note. If you disconnect, the running recording is saved as it is. "
                        "To keep the continuous stream on screen as well, press <b>Save now</b>.")),
        "longrun": ("Long tests (days, weeks)", "Read this before leaving the computer recording for days.",
            "<h3>What the app does on its own</h3>"
            + S(("✓", "Interim saves", "In live monitor mode the recording is written to the same file at your interval (default 5 min)."),
                ("✓", "Splitting", "At 1 million rows a new file is started so memory never fills up; the old part is saved."),
                ("✓", "No sleep", "While connected the computer does not go to sleep (the screen may turn off)."),
                ("✓", "Raw backup", "Every byte is written to disk every half second; a new part starts at 256 MB."),
                ("✓", "USB drops", "The data so far is saved and the app reconnects when the board is back."),
                ("✓", "Disk watch", "A warning banner appears if free space drops below 500 MB."))
            + "<h3>Before you leave the test running, on Windows</h3>"
            + S((1, "Pause updates", "<b>Settings → Windows Update → Pause updates</b>, otherwise Windows may restart at night."),
                (2, "Turn off sleep", "<b>Settings → System → Power</b>: sleep <b>Never</b>. On a laptop set closing the lid to <b>Do nothing</b>."),
                (3, "Disable USB suspend", "<b>Control Panel → Power Options → Plan settings → Advanced</b> → <i>USB selective suspend</i>: <b>Disabled</b>."),
                (4, "Choose CSV", "Much faster than Excel for large recordings and smaller on disk."))
            + TIP("💾", "Rough disk need: <b>line length × lines per second × duration × 2</b> (raw backup + recording file). "
                        "Example: 20-byte lines, 100 Hz, 3 weeks ≈ 7 GB.")),
        "chart": ("Chart and terminal", "See the data on the chart and raw in the terminal.",
            "{img:chart|Chart and terminal}"
            + S((1, "X axis", "<i>Auto</i>, sample number, time (s) or PC time."),
                (2, "Window", "How many recent seconds to show in live mode."),
                (3, "Fit", "Shows all data again after zooming."),
                (4, "Save PNG", "Saves the chart as an image for reports."),
                (5, "Legend", "Click a channel name to hide or show it."),
                (6, "HEX / Pause / Clear", "<b>HEX</b> shows received bytes in hexadecimal (useful with a wrong baud rate or binary data). Clear: <b>Ctrl+L</b>. Recording is not affected."),
                (7, "Quick command buttons", "Commands you send often, one click away. Add with <b>＋</b>, right-click to edit / delete. With a repeat interval the button stays pressed and sends the command every X seconds."),
                (8, "Send command", "Sends text to the board (e.g. <code>SEND</code>). With <b>HEX</b> ticked it sends bytes like <code>01 A0 FF</code>. <b>↑ / ↓</b> recalls previous commands."),
                (9, "Hide / show chart", "Click <b>▾ Chart</b> to fold the chart away and give its space to the terminal, for reading the terminal only. While hidden the chart is not drawn at all, which saves CPU; recording continues."))
            + TIP("🖱️", "Wheel: zoom · Left-drag: pan · Right-click: axis options. The view never runs off the data: "
                        "you can zoom out until all data fits, and panning stops just past the data.")),
        "analysis": ("Analysis: FFT, cursors, statistics, notes", "Inspect the recording without leaving the app.",
            "{img:analysis|Analysis tools}"
            + S((1, "📏 Cursors", "Places lines A and B on the chart. Drag them with the mouse."),
                (2, "Readout", "Values at A and B, the difference (Δ) and <b>1/Δ</b> frequency. Put them at the start and end of one period to read the frequency."),
                (3, "📊 FFT", "Frequency content of the visible part of the chart. Shows the dominant frequency in vibration, balancing or motor measurements."),
                (4, "FFT chart", "Enter the <b>sample rate</b> for correct Hz. Zoom in to analyse only that range."),
                (5, "📝 Add note", "Adds a note at that moment while recording (Ctrl+M). Shown as a yellow line on the chart, in the <b>Note</b> column in Excel and in the <b>Notes</b> tab."),
                (6, "Note line", "The moment the note was added."),
                (7, "Statistics", "Last value, min, max, mean, <b>RMS</b>, standard deviation and, with FFT on, the peak frequency for each channel — over the visible range."))
            + "<h3>Channels and units</h3>"
            "<p>Use <b>Data settings → Channels and units…</b> to convert raw values to real units: "
            "<b>real value = raw × scale + offset</b>. <b>ADC → Volt…</b> computes the scale from the reference voltage and bit count "
            "(12-bit, 3.3 V → 0.000806). The chart, statistics and saved files use the converted values; the column is named "
            "<code>Channel 1 [V]</code>. The raw backup never changes.</p>"
            + TIP("💡", "Example: an LM35 temperature sensor gives 10 mV/°C. With a 12-bit ADC at 3.3 V the scale is 0.000806 × 100 = 0.0806, unit °C.")
            + "<h3>Threshold alarms</h3>"
            "{img:alarms_dlg|Threshold alarms window}"
            "<p><b>Data settings → Threshold alarms…</b>: pick a channel, a condition (rises above / falls below) and a threshold. "
            "The threshold is compared with the unit-converted value (e.g. 3.0 V).</p>"
            "{img:alarms|When an alarm fires}"
            + S((1, "Warning banner", "Which channel crossed its threshold, at what value and when."),
                (2, "Threshold line", "A dashed red line on the chart."),
                (3, "Automatic note", "A note is added on the row where the alarm fired: yellow line on the chart, <b>Note</b> column in Excel."))
            + TIP("🔔", "<b>Hysteresis</b>: the alarm re-arms only after the value moves back by this much, so a signal jittering around the threshold does not flood alarms. "
                        "<b>Sound</b> and <b>command to board</b> (e.g. <code>STOP</code>) are optional; the same alarm produces at most one note, sound and command per second. "
                        "<b>Start rec.</b>: saves the event to its own file when the alarm fires (see <i>Saving and files → Triggered recording</i>).")),
        "look": ("Theme and shortcuts", "Use the app in the colors you like.",
            "<p>Use the colored round button at the top right:</p>"
            + S(("🌗", "Appearance", "<b>Dark</b>, <b>Light</b> or <b>System</b> (follows Windows' dark/light setting)."),
                ("🎨", "Color", "<b>Oskilo</b> (the logo's navy and teal, default), <b>Neutral</b> (classic gray), blue, pink, purple, green, orange or teal. With a color, the whole screen — background, cards, chart, terminal — takes on its shades. "
                        "On Windows 11 the window title bar and message boxes take the same color (dark/light on Windows 10)."),
                ("▾", "A tidier screen", "Click the title of a card on the left (Connection, Data settings, Saving) to fold it; a short summary shows next to the title. "
                        "The chart can be hidden with <b>▾ Chart</b> too. The app remembers which ones are open."))
            + "<h3>Shortcuts</h3>"
            + TABLE(["Key", "Action"],
                    [["<b>F5</b>", "Connect / disconnect"], ["<b>Ctrl+S</b>", "Save now"],
                     ["<b>Ctrl+Shift+S</b>", "Save as"], ["<b>Ctrl+O</b>", "Open saved file"],
                     ["<b>Ctrl+L</b>", "Clear terminal"], ["<b>Ctrl+M</b>", "Add note"], ["<b>↑ / ↓</b>", "Previous commands"],
                     ["<b>F1</b>", "This guide"]])),
        "trouble": ("Troubleshooting", "A red banner and a red Missing count mean some lines never arrived.",
            "{img:trouble|A transfer with lost data}"
            + S((1, "Red banner", "Says how many samples are missing."),
                (2, "Missing", "Number of lost samples and gaps."),
                (3, "Lost range", "Marked red on the chart; no fake values are drawn."))
            + TABLE(["Symptom", "Likely cause and fix"],
                    [["Garbage characters, no numbers", "The <b>baud rate</b> does not match your firmware."],
                     ["Data \"jumps\" after some point", "On STM32 the ADC DMA is in <b>Circular</b> mode. Use <b>Normal</b> and call <code>HAL_ADC_Stop_DMA</code> before sending."],
                     ["Missing data says \"not checked\"", "The board sends no sequence number or <code>START,N</code> (see <i>Board side: data format</i>)."],
                     ["One transfer split into two files", "<b>Transfer end timeout</b> is too short; use 1–2 s."],
                     ["Could not open port", "The port is open in another program, or the board was unplugged."],
                     ["\"Connection lost, reconnecting…\"", "Loose cable or the board restarted. Data was saved; if it keeps happening try another cable / USB port."],
                     ["The app behaves oddly", "<b>? → Reset settings</b> (if it does not open: <code>Oskilo.exe --reset-settings</code>)."]])
            + "<p>Still stuck? Use <b>? → Report a problem / suggestion</b>.</p>"),
        "security": ("Security & Windows warning", "The app never asks for administrator rights or changes system settings.",
            "<h3>Checking that the files are intact</h3>"
            + S(("✓", "Public build", "The app is built only by the public GitHub build system from this repository; every release has build provenance."),
                ("✓", "Verify files", "<b>? → About → Verify files</b> compares every file's fingerprint (SHA-256) with the build and lists modified, missing or added files."),
                ("✓", "Verify the download", "Strongest check: the zip's fingerprint must match <code>SHA256SUMS.txt</code> on the release page (PowerShell: <code>Get-FileHash Oskilo_Windows.zip</code>)."))
            + "<h3>\"Do you want to run this file?\" warning</h3>"
            "<p>Windows shows this for programs downloaded from the internet that are <b>not digitally signed</b>. Oskilo is open source; the warning only appears because it has no signing certificate yet.</p>"
            + S(("1", "Blue screen", "<b>More info → Run anyway</b>. Windows remembers that choice."),
                ("2", "First launch", "The app tells you the status: if the mark is gone it just informs you; otherwise, with your permission, it removes it <b>only from its own unmodified files</b>."),
                ("3", "Sharing with others", "Before extracting, right-click the zip → <b>Properties</b> → <b>Unblock</b> → OK."))
            + "<h3>macOS</h3><p><b>System Settings → Privacy &amp; Security → Open Anyway</b> (first launch only). The macOS build is for Apple Silicon (M1 and later).</p>"
            "<h3>Privacy</h3><p>No data is ever sent to the internet. The only exception: <b>Check for updates</b> reads the latest version number from GitHub.</p>"),
    },
}

_IMG_RE = re.compile(r"\{img:(\w+)(?:\|([^}]*))?\}")


def topics(lang: str) -> list[tuple[str, str]]:
    """[(anahtar, başlık)]"""
    table = GUIDE.get(lang, GUIDE["tr"])
    return [(k, table[k][0]) for k in TOPICS]


def body_html(lang: str, key: str, img=600, img_exists=lambda name: True) -> str:
    """Konunun HTML'i.

    img: genişlik (sayı) ya da img(ad) -> <img ...> etiketi döndüren fonksiyon (arayüz net görsel üretir).
    """
    table = GUIDE.get(lang, GUIDE["tr"])
    title, lead, body = table[key]
    hint = "büyütmek için tıklayın" if lang == "tr" else "click to enlarge"

    def repl(m):
        name, cap = m.group(1), _html.escape(m.group(2) or "")
        if not img_exists(name):
            return ""
        tag = img(name) if callable(img) else f'<img src="{lang}/{name}.png" width="{img}">'
        # <div>: "p" satır aralığı (%150) görselin altında boşluk bırakmasın
        return (f'<div align="center" style="margin-top:10px"><a href="img:{name}">{tag}</a></div>'
                f'<div align="center" class="cap" style="margin-bottom:6px">{cap} · 🔍 {hint}</div>')

    return f'<h2>{title}</h2><p class="lead">{lead}</p>' + _IMG_RE.sub(repl, body)


def image_names() -> set[str]:
    out = set()
    for lang in GUIDE.values():
        for _, _, body in lang.values():
            out |= {m.group(1) for m in _IMG_RE.finditer(body)}
    return out
