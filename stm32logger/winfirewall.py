"""Windows Güvenlik Duvarı: kartlardan gelen paketlere izin veren dar bir kural (yönetici izniyle).

Kullanıcı "Oskilo'ya izin ver" düğmesine basınca tek bir UAC penceresi açılır ve PowerShell ile:
  1) Bu programa ait GELEN yöndeki ENGELLE kuralları silinir. (İlk açılışta Windows'un sorduğu pencere
     kapatıldıysa Windows kendiliğinden engelle kuralı ekler; engelle kuralı izin kuralından üstündür.)
     Kullanıcının bu programa verdiği izin kurallarına dokunulmaz.
  2) Önceki "Oskilo" kuralları (yalnızca bu program için olanlar) silinir, yenisi eklenir:
       UDP, yerel port 5000 (elle adres → UDP dinleme), sadece yerel ağdan (LocalSubnet), bütün ağ türlerinde.
Keşif cevapları, TCP bağlantısı ve UDP abonelik verisi Oskilo'nun önce gönderdiği paketlere cevap
olduğu için Windows bunlara zaten izin verir; onlar için kural gerekmez. (Karşı port üzerinden izin
vermek güvensiz olurdu: gönderen kaynak portunu istediği gibi seçebilir.)
Kaynak koddan çalışırken kural Python'a verilir; bu yüzden kural tek portla ve yerel ağla sınırlıdır.
Başka hiçbir ayara dokunulmaz.
"""
from __future__ import annotations

import base64
import os
import subprocess
import sys

RULE_NAME = "Oskilo"
LOCAL_PORT = 5000


def supported() -> bool:
    return sys.platform == "win32"


def program_path() -> str:
    """Kuralın verileceği program: paketli sürümde Oskilo.exe, kaynak koddan pythonw.exe / python.exe.

    Windows'ta sanal ortamdaki (.venv) python.exe sadece bir yönlendiricidir; soketi açan asıl süreç
    Python kurulumundaki python(w).exe'dir. Kural ona verilmeli (sys._base_executable).
    """
    return program_paths()[0]


def program_paths() -> list[str]:
    """Kural verilecek bütün programlar (kaynak koddan çalışırken python.exe ve pythonw.exe)."""
    if getattr(sys, "frozen", False):
        return [os.path.abspath(sys.executable)]
    base = os.path.abspath(getattr(sys, "_base_executable", "") or sys.executable)
    out = [base]
    folder = os.path.dirname(base)
    for name in ("pythonw.exe", "python.exe"):
        p = os.path.join(folder, name)
        if p.lower() != base.lower() and os.path.isfile(p):
            out.append(p)
    return out


def _no_window() -> dict:
    if sys.platform == "win32":
        return {"creationflags": 0x08000000}          # CREATE_NO_WINDOW
    return {}


def rule_exists(path: str | None = None) -> bool:
    """Bu program için 'Oskilo' adlı izin kuralı var mı? (Yönetici izni gerekmez.)"""
    if not supported():
        return False
    path = path or program_path()
    try:
        out = subprocess.run(["netsh", "advfirewall", "firewall", "show", "rule", f"name={RULE_NAME}",
                              "verbose"], capture_output=True, timeout=8, **_no_window())
    except (OSError, subprocess.SubprocessError):
        return False
    text = out.stdout.decode("oem" if sys.platform == "win32" else "utf-8", errors="replace").lower()
    return out.returncode == 0 and path.lower() in text


def _ps_quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def build_script(paths: list[str]) -> str:
    """Yönetici olarak çalışacak PowerShell betiği (test edilebilsin diye ayrı)."""
    progs = ", ".join(_ps_quote(p) for p in paths)
    return f"""$ErrorActionPreference = 'Stop'
try {{
  $progs = @({progs})
  foreach ($p in $progs) {{
    Get-NetFirewallApplicationFilter -Program $p -ErrorAction SilentlyContinue | Get-NetFirewallRule |
      Where-Object {{ $_.Direction -eq 'Inbound' -and $_.Action -eq 'Block' }} | Remove-NetFirewallRule
  }}
  Get-NetFirewallRule -DisplayName {_ps_quote(RULE_NAME)} -ErrorAction SilentlyContinue |
    Where-Object {{ $progs -contains (($_ | Get-NetFirewallApplicationFilter).Program) }} | Remove-NetFirewallRule
  foreach ($p in $progs) {{
    New-NetFirewallRule -DisplayName {_ps_quote(RULE_NAME)} -Direction Inbound -Action Allow -Program $p `
      -Protocol UDP -LocalPort {LOCAL_PORT} -RemoteAddress LocalSubnet -Profile Any | Out-Null
  }}
  exit 0
}} catch {{
  exit 1
}}
"""


def encoded(script: str) -> str:
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def allow_program(paths: list[str] | None = None, timeout_ms: int = 30000) -> tuple[str, str]:
    """İzin kuralını ekler. Dönüş: ('ok'|'cancel'|'fail'|'notwin', ayrıntı)."""
    if not supported():
        return "notwin", ""
    paths = paths or program_paths()
    import ctypes
    from ctypes import wintypes

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", wintypes.ULONG), ("hwnd", wintypes.HWND),
                    ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR),
                    ("lpParameters", wintypes.LPCWSTR), ("lpDirectory", wintypes.LPCWSTR),
                    ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE), ("lpIDList", ctypes.c_void_p),
                    ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HANDLE),
                    ("dwHotKey", wintypes.DWORD), ("hIconOrMonitor", wintypes.HANDLE),
                    ("hProcess", wintypes.HANDLE)]

    SEE_MASK_NOCLOSEPROCESS = 0x00000040
    SEE_MASK_NOASYNC = 0x00000100
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
    shell32.ShellExecuteExW.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    ps_exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "WindowsPowerShell",
                          "v1.0", "powershell.exe")
    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC
    info.lpVerb = "runas"
    info.lpFile = ps_exe
    info.lpParameters = ("-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden "
                         "-EncodedCommand " + encoded(build_script(paths)))
    info.nShow = 0                                      # SW_HIDE
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        err = ctypes.get_last_error()
        if err == 1223:                                 # ERROR_CANCELLED: kullanıcı "Hayır" dedi
            return "cancel", ""
        return "fail", str(ctypes.WinError(err))
    if not info.hProcess:
        return "fail", "process"
    try:
        if kernel32.WaitForSingleObject(info.hProcess, timeout_ms) != 0:
            return "fail", "timeout"
        code = wintypes.DWORD()
        kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
    finally:
        kernel32.CloseHandle(info.hProcess)
    if code.value != 0:
        return "fail", f"PowerShell {code.value}"
    return "ok", ""
