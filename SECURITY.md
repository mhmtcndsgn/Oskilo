# Security Policy / Güvenlik Politikası

**🇬🇧 English** · [🇹🇷 Türkçe](#-türkçe)

## Reporting a vulnerability

Please **do not** open a public issue for security problems.
Use GitHub's private reporting instead: **Security → Report a vulnerability** on this repository
([direct link](https://github.com/mhmtcndsgn/Oskilo/security/advisories/new)).
You will get an answer within a few days. Only the latest release receives fixes.

## How releases are protected

- **Built only by GitHub Actions** from the public source code in this repository ([workflow](.github/workflows/build.yml)). No release is ever built on a personal computer.
- **Pinned build tools:** every GitHub Action is pinned to an immutable commit SHA, and every Python package is installed from [`requirements-lock.txt`](requirements-lock.txt) with SHA-256 hash checking (`pip --require-hashes`).
- **Least privilege:** the workflow token is read-only; write access exists only in the final release step.
- **Build provenance:** every release file has a signed GitHub [artifact attestation](https://docs.github.com/actions/security-for-github-actions/using-artifact-attestations). Verify with:
  ```
  gh attestation verify Oskilo_Windows.zip -R mhmtcndsgn/Oskilo
  ```
- **Checksums:** `SHA256SUMS.txt` is attached to every release.
  Windows: `Get-FileHash Oskilo_Windows.zip` · macOS/Linux: `shasum -a 256 Oskilo_macOS.dmg`
- **File integrity inside the app:** the Windows build contains the SHA-256 fingerprint of every file (`_internal/manifest.sha256`). **? → About → Verify files** reports modified, missing or unknown (added) files in the app folder. The automatic "Unblock" only removes Windows' download mark from files that match these fingerprints. This catches corruption and casual tampering; because the list lives in the same folder, the strong guarantees are `SHA256SUMS.txt` and the attestation checked at download time.
- **End-to-end self-test:** each build is started in CI and must record a 4000-sample DEMO transfer with zero loss and a clean integrity check before it can be released.

## What the app does and does not do

- Needs **no administrator rights**, installs no service or driver, changes no system settings.
- Communicates only with the serial port you select and writes only to the folder you choose (plus a small crash log: Windows `%LOCALAPPDATA%\Oskilo`, macOS `~/Library/Logs/Oskilo`).
- Makes **one** network request, and only when you click *Check for updates*: it reads the latest version number from the GitHub API (HTTPS only, size-limited) and can open this repository's Releases page in your browser. It never downloads or runs anything by itself.
- Treats all data from the board as untrusted: long or malformed lines are ignored, channel count is limited (64), numbers that overflow become empty cells, and channel names are written to CSV/Excel in a way that cannot run as a formula.

## Antivirus false positives

Unsigned, newly released programs packaged with PyInstaller are sometimes flagged by antivirus heuristics.
If that happens: check the SHA-256 against `SHA256SUMS.txt`, run **Verify files**, and please open an issue so we can submit the file to the antivirus vendor as a false positive.

---

## 🇹🇷 Türkçe

### Güvenlik açığı bildirmek
Güvenlik sorunları için lütfen herkese açık *issue* **açmayın**. Bu repoda **Security → Report a vulnerability** ile gizli olarak bildirin ([doğrudan bağlantı](https://github.com/mhmtcndsgn/Oskilo/security/advisories/new)). Birkaç gün içinde yanıt verilir. Düzeltmeler sadece son sürüme gelir.

### Sürümler nasıl korunuyor?
- Sürümler **sadece GitHub Actions** üzerinde, bu repodaki açık kaynak koddan derlenir; kişisel bilgisayarda derlenmez.
- Derleme araçları **sabitlenmiştir**: her GitHub Action değişmez bir commit SHA'sıyla, her Python paketi `requirements-lock.txt` içindeki SHA-256 parmak iziyle kurulur.
- İş akışının yetkisi sadece **okuma**dır; yazma yetkisi yalnızca son yayın adımında vardır.
- Her sürüm dosyası için imzalı **derleme kanıtı** (artifact attestation) üretilir: `gh attestation verify Oskilo_Windows.zip -R mhmtcndsgn/Oskilo`
- Her sürümde **`SHA256SUMS.txt`** vardır. Windows: `Get-FileHash Oskilo_Windows.zip`
- Windows paketinde her dosyanın parmak izi bulunur. **? → Hakkında → Dosyaları doğrula** uygulama klasöründe değişen, eksik ya da **sonradan eklenen** dosyaları gösterir. Otomatik "Engellemeyi kaldır" da sadece parmak izi tutan dosyalara uygulanır. Bu, bozulmayı ve basit değişiklikleri yakalar; liste aynı klasörde durduğu için asıl güvence indirme anında kontrol edilen `SHA256SUMS.txt` ve derleme kanıtıdır.
- Her derleme CI'da çalıştırılır; 4000 satırlık DEMO aktarımını kayıpsız alıp bütünlük kontrolünü geçmeden yayınlanamaz.

### Uygulama ne yapar, ne yapmaz?
- **Yönetici izni istemez**, servis/sürücü kurmaz, sistem ayarlarını değiştirmez.
- Sadece seçtiğiniz seri portla haberleşir ve seçtiğiniz klasöre yazar (ayrıca küçük bir hata kaydı: Windows'ta `%LOCALAPPDATA%\Oskilo`, macOS'ta `~/Library/Logs/Oskilo`).
- Tek ağ isteği, siz *Güncellemeleri kontrol et*'e bastığınızda GitHub'dan son sürüm numarasını okumaktır (sadece HTTPS, boyutu sınırlı). Kendiliğinden hiçbir şey indirmez ya da çalıştırmaz.
- Karttan gelen veriye güvenmez: çok uzun/bozuk satırlar yok sayılır, kanal sayısı 64 ile sınırlıdır, taşan sayılar boş hücre olur, kanal adları CSV/Excel'de formül olarak çalışamayacak şekilde yazılır.

### Antivirüs yanlış alarmı
İmzasız ve yeni yayınlanmış programlar bazen antivirüs tarafından yanlışlıkla şüpheli bulunur. Böyle olursa SHA-256 değerini `SHA256SUMS.txt` ile karşılaştırın, **Dosyaları doğrula**'yı çalıştırın ve bir *issue* açın; dosyayı antivirüs firmasına yanlış alarm olarak bildirelim.
