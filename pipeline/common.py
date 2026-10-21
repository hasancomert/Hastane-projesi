"""Veri hattının ortak sabitleri ve yardımcıları.

Tüm betikler repo kökünden çalıştırılır:  .venv/bin/python pipeline/<betik>.py
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATA = ROOT / "data"
RAW = DATA / "raw"                  # git dışı ham indirmeler
REFERENCE = DATA / "reference"      # kaynaklı küçük tablolar (CSV/YAML)
PARAMS_DIR = REFERENCE / "params"   # alan bazında parametre parçaları
SOURCES_DIR = DATA / "sources"      # alan bazında kaynak parçaları
PROCESSED = DATA / "processed"      # git'e giren işlenmiş veriler
BASEMAP = DATA / "basemap"

# Koordinat sistemleri
CRS_WGS84 = "EPSG:4326"     # harita
CRS_UTM36N = "EPSG:32636"   # metrik hesaplar (mesafe, ızgara)
CRS_MOLLWEIDE = "ESRI:54009"  # GHS-POP'un yerel ızgarası
CRS_EQUAL_AREA = "EPSG:6933"  # alan karşılaştırması (eşit alanlı)

# Ankara il sınırını kapsayan kutu (WGS84, biraz pay bırakılmış)
ANKARA_BBOX = (30.8, 38.6, 34.2, 40.8)

# GHS-POP 100 m / 1 km ızgarasına hizalı Ankara penceresi (Mollweide, metre).
# Kökene (-18.041.000; 9.000.000) göre 1 km katlarında; il sınırının her yanında ≈2 km pay.
GHS_ORIGIN = (-18_041_000, 9_000_000)
GHS_WINDOW = (2_614_000, 4_637_000, 2_906_000, 4_876_000)  # (xmin, ymin, xmax, ymax)

TUIK_PROVINCE_POP_2025 = 5_910_320  # Bölüm 3; Faz 1'de TÜİK'ten teyit edilir

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0 Safari/537.36"
)


def today() -> str:
    return dt.date.today().isoformat()


def sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def download(url: str, dest: Path, *, timeout: int = 120, overwrite: bool = False) -> dict:
    """URL'yi dest'e indirir; manifest kaydı (url, tarih, boyut, sha256) döndürür.

    Kayıt dest'in yanındaki `_manifest.jsonl` dosyasına da eklenir.
    """
    import requests

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if overwrite or not dest.exists():
        with requests.get(url, headers={"User-Agent": USER_AGENT}, stream=True,
                          timeout=timeout) as r:
            r.raise_for_status()
            tmp = dest.with_suffix(dest.suffix + ".part")
            with open(tmp, "wb") as f:
                for block in r.iter_content(1 << 20):
                    f.write(block)
            tmp.replace(dest)
    rec = {
        "url": url,
        "file": str(dest.relative_to(ROOT)) if dest.is_relative_to(ROOT) else str(dest),
        "accessed": today(),
        "bytes": dest.stat().st_size,
        "sha256": sha256(dest),
    }
    with open(dest.parent / "_manifest.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


_TR_MAP = str.maketrans({
    "ç": "c", "Ç": "c", "ğ": "g", "Ğ": "g", "ı": "i", "I": "i", "İ": "i",
    "ö": "o", "Ö": "o", "ş": "s", "Ş": "s", "ü": "u", "Ü": "u",
    "â": "a", "Â": "a", "î": "i", "Î": "i", "û": "u", "Û": "u",
})


def norm_name(name: str) -> str:
    """Türkçe adları eşleştirme anahtarına çevirir: 'Balâ' -> 'bala', 'Şereflikoçhisar' -> 'sereflikochisar'."""
    s = unicodedata.normalize("NFC", str(name)).strip().translate(_TR_MAP).lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", s)


def fmt_tr(x: float, decimals: int = 0) -> str:
    """Türkçe sayı biçimi: 1234567.8 -> '1.234.567,8'."""
    s = f"{x:,.{decimals}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")
