"""Windows exe'sine ürün adı / sürüm / telif bilgisini gömmek için PyInstaller sürüm dosyası üretir.

Kullanım (derleme öncesi):  python packaging/make_version_info.py packaging/version_info.txt
Sürüm stm32logger/__init__.py içindeki __version__ değerinden okunur.
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
src = (ROOT / "stm32logger" / "__init__.py").read_text(encoding="utf-8")
version = re.search(r'__version__\s*=\s*"([^"]+)"', src).group(1)
parts = [int(x) for x in re.findall(r"\d+", version)][:4]
parts += [0] * (4 - len(parts))
vt = tuple(parts)

TEMPLATE = f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(filevers={vt}, prodvers={vt}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'mhmtcndsgn'),
      StringStruct('FileDescription', 'Oskilo - serial data logger for STM32, Arduino, ESP32'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', 'Oskilo'),
      StringStruct('LegalCopyright', 'Copyright (c) 2026 Mehmet Can. MIT License.'),
      StringStruct('OriginalFilename', 'Oskilo.exe'),
      StringStruct('ProductName', 'Oskilo'),
      StringStruct('ProductVersion', '{version}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""

out = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "packaging" / "version_info.txt")
out.write_text(TEMPLATE, encoding="utf-8")
print(f"{out} -> {version}")
