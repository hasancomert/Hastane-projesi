"""Sunucu yapılandırması: yollar, sınırlar, motor sürümü.

Tüm yollar pathlib ile depo köküne göre (Windows uyumlu). Sınırlar ortam değişkenleriyle değiştirilebilir (testler ve
start.bat için); varsayılanlar aşağıda gerekçeleriyle.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
REFERENCE_DIR = DATA_DIR / "reference"
PROCESSED_DIR = DATA_DIR / "processed"
GRID_DIR = PROCESSED_DIR / "grid"
WEB_DIR = PROCESSED_DIR / "web"
BASEMAP_DIR = DATA_DIR / "basemap"
SOURCES_DIR = DATA_DIR / "sources"
PRECOMPUTED_DIR = ROOT / "precomputed"
STATIC_DIR = ROOT / "backend" / "static"
CACHE_DIR = ROOT / "cache"
MODEL_DIR = ROOT / "backend" / "model"
SCENARIOS_YAML = REFERENCE_DIR / "senaryolar.yaml"

API_VERSION = "1"
ENGINE_TAG = "faz4"
GRID_SIZES = (2, 3, 5)

# Vite geliştirme sunucusu (şartname 5: frontend React + Vite); başka köken yok
CORS_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")
# Host başlığı beyaz listesi (DNS yeniden bağlama saldırısına karşı; sunucu yalnız 127.0.0.1'e bağlanır)
ALLOWED_HOSTS = ("127.0.0.1", "localhost")
# Izgara başına önerilen (ve süre sınırı verilmeyen istekte kullanılan) canlı süre sınırı (sn): şartname 4.6 hedefleri
# (3 km ≲ 15 sn, 2 km ≲ 60 sn); 5 km için kayıttaki solver.time_limit_s (15 sn). Faz 3 kıyaslaması bu sınırlarla
# yapıldı (docs/FAZ3_RAPOR.md §5).
RECOMMENDED_TIME_LIMIT_S = {2: 60.0, 3: 15.0, 5: 15.0}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """Sunucu ayarları (iş kuyruğu, bekçi, önbellek). Model parametresi değildir."""

    cache_dir: Path = field(default_factory=lambda: Path(os.environ.get("HASTANE_CACHE_DIR", CACHE_DIR)))
    static_dir: Path = field(default_factory=lambda: Path(os.environ.get("HASTANE_STATIC_DIR", STATIC_DIR)))
    # kuyruk: canlı arayüzde aynı anda birkaç iş yeter; fazlası yığılma demektir (429)
    max_queued: int = field(default_factory=lambda: _env_int("HASTANE_MAX_QUEUED", 8))
    max_queued_per_client: int = field(default_factory=lambda: _env_int("HASTANE_MAX_QUEUED_CLIENT", 4))
    max_jobs_kept: int = 300                 # bellekte tutulan bitmiş iş sayısı (en eskiler atılır)
    max_results_kept: int = 20               # sonucu bellekte tutulan son bitmiş iş sayısı (2 km sonucu Python
                                             # nesneleri olarak birkaç MB; eskilerin sonucu önbellekte kalır)
    # bekçi: çalışan iş beklenen süresini (iş türü verir) bu kadar aşarsa ya da kalp atışı kesilirse süreç yeniden başlar
    heartbeat_s: float = 1.0                 # işçi → sunucu kalp atışı aralığı
    heartbeat_timeout_s: float = field(default_factory=lambda: _env_float("HASTANE_HEARTBEAT_TIMEOUT", 30.0))
    cancel_grace_s: float = field(default_factory=lambda: _env_float("HASTANE_CANCEL_GRACE", 15.0))
    watchdog_extra_s: float = field(default_factory=lambda: _env_float("HASTANE_WATCHDOG_EXTRA", 30.0))
    worker_start_timeout_s: float = 120.0    # işçinin modülleri yükleyip 'hazır' demesi için (Windows'ta soğuk içe aktarma)
    shutdown_grace_s: float = 5.0
    sse_heartbeat_s: float = 2.0             # SSE 'heartbeat' olayı aralığı
    sse_progress_min_interval_s: float = 0.2  # işçide ilerleme olaylarının en sık gönderimi (aşama/çözüm olayları hariç)
    # önbellek: 3 km sonucu ≈0,35 MB, 2 km ≈0,6 MB (sıkıştırmasız JSON); gzip ile ≈5 kat küçük
    cache_max_bytes: int = field(default_factory=lambda: _env_int("HASTANE_CACHE_MAX_BYTES", 300 * 1024 * 1024))
    cache_max_entries: int = field(default_factory=lambda: _env_int("HASTANE_CACHE_MAX_ENTRIES", 500))
    seed_precomputed: bool = True
    start_worker: bool = True
    allowed_hosts: tuple[str, ...] = field(default_factory=lambda: tuple(
        h.strip() for h in os.environ.get("HASTANE_ALLOWED_HOSTS", ",".join(ALLOWED_HOSTS)).split(",") if h.strip()))
    # istek gövdesi sınırı: en büyük meşru gövde (10.000 yasak kilidi ≈0,3 MB; 2.000 hastane ≈0,1 MB) bunun çok altında
    max_body_bytes: int = field(default_factory=lambda: _env_int("HASTANE_MAX_BODY_BYTES", 2 * 1024 * 1024))
    # işçide yedek HiGHS alt süreci (backend/model/milp.py set_prespawn); HASTANE_PRESPAWN=0 kapatır
    prespawn_highs: bool = field(default_factory=lambda: os.environ.get("HASTANE_PRESPAWN", "1") != "0")

    def with_(self, **kw) -> Settings:
        return replace(self, **kw)


_ENGINE_VERSION: str | None = None


def engine_version() -> str:
    """Önbellek anahtarındaki motor sürümü: motor kaynak dosyaları + ızgara verisinin özeti.

    Motor kodu ya da ızgara verisi değişirse eski önbellek kayıtları kendiliğinden geçersiz olur."""
    global _ENGINE_VERSION
    if _ENGINE_VERSION is None:
        h = hashlib.sha256()
        files = sorted(MODEL_DIR.glob("*.py")) + sorted(GRID_DIR.glob("grid_*km.npz"))
        for f in files:
            h.update(f.name.encode())
            # satır sonları normalleştirilir (Windows'ta git autocrlf aynı kodu farklı baytlarla yazabilir)
            data = f.read_bytes()
            h.update(data.replace(b"\r\n", b"\n") if f.suffix == ".py" else data)
        _ENGINE_VERSION = f"{ENGINE_TAG}-{h.hexdigest()[:12]}"
    return _ENGINE_VERSION
