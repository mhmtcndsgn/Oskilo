; SPDX-License-Identifier: GPL-3.0-or-later
; Copyright (c) 2026 Mehmet Can Dusgun (mhmtcndsgn) — Oskilo
;
; Oskilo Windows kurulum programı (Inno Setup 6).
;
; - Yönetici izni İSTEMEZ: kullanıcının kendi klasörüne kurar (%LOCALAPPDATA%\Programs\Oskilo).
;   Bu sayede uygulama içi güncelleme (Yardım > Güncellemeleri denetle) kurulu sürümde de çalışır.
; - Başlat menüsü kısayolu, isteğe bağlı masaüstü kısayolu, Türkçe / İngilizce.
; - "Uygulamalar"dan kaldırılabilir. Kaldırırken ayarların silinip silinmeyeceği sorulur;
;   kayıtlarınız (Belgeler\Oskilo) hiçbir zaman silinmez.
;
; Derleme (CI yapar):  ISCC /DMyAppVersion=4.0.0 /DSourceDir=..\dist\Oskilo /O..\ packaging\oskilo.iss

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\Oskilo"
#endif
#define MyAppName "Oskilo"
#define MyAppExe "Oskilo.exe"
#define MyAppPublisher "Mehmet Can Dusgun (mhmtcndsgn)"
#define MyAppURL "https://github.com/mhmtcndsgn/Oskilo"
; Değiştirmeyin: Windows kurulu programı bu kimlikle tanır (güncelleme sürüm numarasını da buradan yazar)
#define MyAppId "C7A79E34-102C-58D8-A91C-73C48CFE1405"

[Setup]
AppId={{{#MyAppId}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
AppCopyright=© 2026 Mehmet Can Dusgun — GPL-3.0-or-later
VersionInfoVersion={#MyAppVersion}
VersionInfoDescription={#MyAppName} kurulum
; kullanıcı klasörüne kurulum, yönetici izni yok
PrivilegesRequired=lowest
DefaultDirName={userpf}\{#MyAppName}
DisableDirPage=yes
DisableProgramGroupPage=yes
UsePreviousAppDir=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
WizardStyle=modern
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExe}
UninstallDisplayName={#MyAppName}
LicenseFile=..\LICENSE
OutputBaseFilename=Oskilo_Setup
Compression=lzma2/max
SolidCompression=yes
; açık olan Oskilo'yu kapatmayı önerir (dosyalar kullanımdayken üzerine yazılamaz)
CloseApplications=yes
RestartApplications=no
ShowLanguageDialog=auto

[Languages]
Name: "tr"; MessagesFile: "compiler:Languages\Turkish.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
tr.DeleteSettings=Oskilo kaldırıldı.%n%nAyarlarınız (kayıt klasörü, birimler, alarmlar, hızlı komutlar) da silinsin mi?%n%nKayıt dosyalarınız (Belgeler\Oskilo) silinmez.
en.DeleteSettings=Oskilo has been removed.%n%nAlso delete your settings (save folder, units, alarms, quick commands)?%n%nYour recordings (Documents\Oskilo) are not deleted.

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; eski sürümün artık kullanılmayan dosyaları kalmasın (paket tek klasördür)
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\_oskilo_new"
Type: filesandordirs; Name: "{app}\_oskilo_old"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; uygulama içi güncellemenin eklediği dosyalar da gitsin (klasör her zaman {userpf}\Oskilo, kullanıcı seçemez)
Type: filesandordirs; Name: "{app}"

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and (not UninstallSilent) then
  begin
    if MsgBox(CustomMessage('DeleteSettings'), mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
    begin
      RegDeleteKeyIncludingSubkeys(HKEY_CURRENT_USER, 'Software\mhmtcndsgn\Oskilo');
      RegDeleteKeyIncludingSubkeys(HKEY_CURRENT_USER, 'Software\mhmtcndsgn\DataLogger');
      RegDeleteKeyIfEmpty(HKEY_CURRENT_USER, 'Software\mhmtcndsgn');
      DelTree(ExpandConstant('{localappdata}\Oskilo'), True, True, True);
    end;
  end;
end;
