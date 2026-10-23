"""İş türü kayıt defteri (sunucu ve işçi süreci ortak kullanır).

Bir iş türü iki yarıdan oluşur:
  * prepare(request) — SUNUCUDA: istek modelini doğrular (hatada ApiValidationError → 422), işçiye gidecek yükü
    (payload, yalnız düz JSON türleri), önbellek anahtarını ve bekçi süresini üretir;
  * run(ctx, payload) — İŞÇİ SÜRECİNDE: motoru çalıştırır, ctx.emit(...) ile ilerleme gönderir, ctx.cancel_event'i
    izler, JSON'a çevrilebilir bir sonuç sözlüğü döndürür (hata: JobError, Türkçe mesaj).

Yeni iş türü (Faz 6: explain / sweep / pareto / sensitivity) eklemek için altyapıya dokunulmaz: backend/app/ altında
adı 'task_' ile başlayan bir modül yazılır ve modül içinde register(JobType(...)) çağrılır. load_all() bu modülleri
hem sunucuda hem işçide içe aktarır; genel uç nokta POST /api/jobs/{tür} yeni türü kendiliğinden sunar.
"""
from __future__ import annotations

import hashlib
import importlib
import pkgutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import orjson
from pydantic import BaseModel


class JobError(Exception):
    """İşçide beklenen hata (mesaj Türkçe, arayüzde gösterilir)."""

    def __init__(self, message_tr: str, detail: Any = None) -> None:
        super().__init__(message_tr)
        self.message_tr = message_tr
        self.detail = detail


class ApiValidationError(Exception):
    """İstek doğrulanamadı → HTTP 422. errors: [{'loc', 'key', 'message_tr', 'allowed', ...}]."""

    def __init__(self, errors: list[dict], message_tr: str = "İstek doğrulanamadı.") -> None:
        super().__init__(message_tr)
        self.errors = errors
        self.message_tr = message_tr


@dataclass
class Prepared:
    """prepare() çıktısı."""

    payload: dict                       # işçiye giden yük (pickle'lanabilir düz türler)
    cache_key: str | None = None        # problem anahtarı (değişke hariç); None → önbelleğe alınmaz
    variant: dict = field(default_factory=dict)   # önbellek değişkesi (ör. süre kovası, gap)
    max_runtime_s: float = 60.0         # bekçi: beklenen en uzun çalışma süresi (+ Settings.watchdog_extra_s)
    warnings: list[str] = field(default_factory=list)
    info: dict = field(default_factory=dict)      # iş özetinde gösterilen kısa bilgi (ızgara, mod, …)


@dataclass
class JobContext:
    """İşçi tarafında çalışan işin bağlamı."""

    job_id: str
    cancel_event: threading.Event
    put: Callable[[tuple], None]        # işçi → sunucu ileti kuyruğu
    min_interval_s: float = 0.2
    t0: float = field(default_factory=time.perf_counter)
    _last: float = -1e9
    _pending: dict | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def elapsed(self) -> float:
        return time.perf_counter() - self.t0

    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def emit(self, data: dict, force: bool = False) -> None:
        """İlerleme olayı; force=False iken en sık min_interval_s'de bir gönderilir. Arada kalan son olay saklanır ve
        işçinin zamanlayıcısı (flush_stale) onu en geç min_interval_s sonra gönderir: olay kaybolmaz, gecikmez."""
        now = time.perf_counter()
        ev = dict(data)
        ev.setdefault("t", round(now - self.t0, 3))
        with self._lock:
            if not force and now - self._last < self.min_interval_s:
                self._pending = ev
                return
            self._pending = None
            self._last = now
        self.put(("progress", self.job_id, ev))

    def flush(self) -> None:
        with self._lock:
            ev, self._pending = self._pending, None
            if ev is not None:
                self._last = time.perf_counter()
        if ev is not None:
            self.put(("progress", self.job_id, ev))

    def flush_stale(self) -> None:
        """Bekleyen olay min_interval_s'den eskiyse gönderir (işçinin zamanlayıcı iş parçacığı çağırır)."""
        with self._lock:
            stale = self._pending is not None and time.perf_counter() - self._last >= self.min_interval_s
        if stale:
            self.flush()


def _exact(cached: dict, requested: dict) -> bool:
    return cached == requested


@dataclass(frozen=True)
class JobType:
    name: str
    request_model: type[BaseModel]
    prepare: Callable[[BaseModel], Prepared]
    run: Callable[[JobContext, dict], dict]
    description_tr: str = ""
    replace_group: str | None = None    # "en son istek kazanır": aynı istemci + aynı grup (varsayılan: tür adı)
    cooperative_cancel: bool = True     # iptal olayını izliyor mu (izlemiyorsa iptal isteğine bekçi süresi boyunca
                                        # yanıt beklenir; işçi ancak o süre de aşılırsa yeniden başlatılır)
    # önbellek: satisfies(önbellekteki değişke, istenen değişke) → istek karşılanır mı. Önbellekteki değişke, istenen
    # değişke ile summarize(sonuç) birleşimidir (ör. {'tl': 15, 'gap': 0.01, 'duals': True, 'ach': 0.004})
    satisfies: Callable[[dict, dict], bool] = _exact
    rank: Callable[[dict], tuple] = lambda variant: ()      # birden çok uygun kayıt varsa en küçük sıra seçilir
    cache_result: Callable[[dict], bool] = lambda result: bool(result.get("ok"))
    summarize: Callable[[dict], dict] = lambda result: {}  # önbellek dizinine yazılan kısa özet (değişkeye eklenir)
    finalize: Callable[[dict, Any], dict] | None = None   # sunucuda sonuç sonrası işlem (ör. başvuru optimumu)
    seed: Callable[[Any], int] | None = None    # sunucu başlarken önbelleğe hazır sonuç ekler (ör. precomputed/)
    # işçi başlarken bir kez ('hazır'dan önce; ağır içe aktarmalar, varsayılan yapı). İsteğe bağlı olarak ertelenmiş
    # adımlar (çağrılabilirler listesi) döndürür; işçi boşken birer birer çalıştırır.
    warmup: Callable[[], list | None] | None = None
    available: bool = True              # False: kayıtlı taslak (istek 501 ile reddedilir; ör. Faz 6 işleri)
    unavailable_tr: str = ""

    @property
    def group(self) -> str:
        return self.replace_group or self.name


REGISTRY: dict[str, JobType] = {}
_loaded = False
_load_lock = threading.Lock()


def register(jt: JobType) -> JobType:
    REGISTRY[jt.name] = jt
    return jt


def load_all() -> dict[str, JobType]:
    """backend/app/task_*.py modüllerini içe aktarır (her süreçte bir kez)."""
    global _loaded
    with _load_lock:
        if not _loaded:
            from backend import app as app_pkg
            for m in sorted(pkgutil.iter_modules(app_pkg.__path__), key=lambda m: m.name):
                if m.name.startswith("task_"):
                    importlib.import_module(f"backend.app.{m.name}")
            _loaded = True
    return REGISTRY


def get(name: str) -> JobType:
    load_all()
    try:
        return REGISTRY[name]
    except KeyError:
        raise KeyError(name) from None


def stable_key(prefix: str, parts: Any) -> str:
    """Kararlı önbellek anahtarı: önek + sıralı JSON'un SHA-256 özeti (24 hex)."""
    raw = orjson.dumps(parts, option=orjson.OPT_SORT_KEYS | orjson.OPT_SERIALIZE_NUMPY, default=str)
    return f"{prefix}-{hashlib.sha256(raw).hexdigest()[:24]}"


# süre sınırı kovaları (sn): aynı kovadaki süre sınırları önbellekte aynı istek sayılır (aşağı yuvarlanır)
TIME_BUCKETS = (1, 2, 3, 5, 8, 10, 15, 20, 30, 45, 60, 90, 120, 180, 300, 600, 900, 1200, 1800, 3600)


def time_bucket(seconds: float) -> float:
    b = TIME_BUCKETS[0]
    for edge in TIME_BUCKETS:
        if seconds + 1e-9 >= edge:
            b = edge
    return float(b)
