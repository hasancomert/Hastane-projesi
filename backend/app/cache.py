"""Disk önbelleği (cache/): iş sonuçları, kararlı anahtar + değişke ile.

Her kayıt iki dosyadır: <ad>.json.gz (sonuç, gzip'li JSON) ve <ad>.meta.json (küçük üst veri: anahtar, değişke, motor
sürümü, boyut, oluşturma zamanı). Başlangıçta yalnız üst veri dosyaları okunur (hızlı). Anahtar motor sürümünü,
etkili parametreleri, ızgarayı, senaryoyu, modu ve kilitleri içerir (iş türü kurar); değişke süre kovası, gap gibi
"çaba" ayarlarıdır ve iş türünün satisfies() kuralıyla eşleşir (daha uzun / daha sıkı çözülmüş kayıt isteği karşılar).

LRU: isabette üst veri dosyasının zamanı güncellenir; boyut ya da kayıt sayısı sınırı aşılınca en eski kayıtlar silinir.
Bozuk ya da yarım dosyalar sessizce yok sayılır ve silinir. Yazma atomiktir (geçici dosya + os.replace).
"""
from __future__ import annotations

import gzip
import hashlib
import logging
import os
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

import orjson

log = logging.getLogger("hastane.cache")

META_SUFFIX = ".meta.json"
DATA_SUFFIX = ".json.gz"


@dataclass
class Entry:
    name: str
    key: str
    variant: dict
    engine: str
    job_type: str
    size: int
    created: float
    atime: float


def _name(key: str, variant: dict) -> str:
    raw = key.encode() + b"|" + orjson.dumps(variant, option=orjson.OPT_SORT_KEYS)
    return hashlib.sha256(raw).hexdigest()[:32]


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


class ResultCache:
    def __init__(self, directory: Path, *, max_bytes: int, max_entries: int, engine: str) -> None:
        self.dir = Path(directory)
        self.max_bytes = int(max_bytes)
        self.max_entries = int(max_entries)
        self.engine = engine
        self._lock = threading.RLock()
        self._entries: dict[str, Entry] = {}
        self._by_key: dict[str, set[str]] = {}
        self.stats = {"hits": 0, "misses": 0, "puts": 0, "evicted": 0, "corrupt": 0, "stale_removed": 0}
        self.dir.mkdir(parents=True, exist_ok=True)
        self._scan()

    # ------------------------------------------------------------------ dizin
    def _scan(self) -> None:
        """Üst veri dosyalarından dizini kurar; bozuk, yarım ya da başka motor sürümüne ait kayıtları siler."""
        for meta_path in self.dir.glob(f"*{META_SUFFIX}"):
            name = meta_path.name[: -len(META_SUFFIX)]
            data_path = self.dir / f"{name}{DATA_SUFFIX}"
            try:
                m = orjson.loads(meta_path.read_bytes())
                e = Entry(name=name, key=str(m["key"]), variant=dict(m["variant"]), engine=str(m["engine"]),
                          job_type=str(m.get("job_type", "")), size=int(m["size"]), created=float(m["created"]),
                          atime=meta_path.stat().st_mtime)
                if not data_path.exists():
                    raise ValueError("veri dosyası yok")
            except Exception:
                self.stats["corrupt"] += 1
                self._remove_files(name)
                continue
            if e.engine != self.engine:
                self.stats["stale_removed"] += 1
                self._remove_files(name)
                continue
            self._add(e)
        # yetim veri dosyaları ve yarım kalmış geçici dosyalar
        for p in list(self.dir.glob(f"*{DATA_SUFFIX}")) + list(self.dir.glob(".*.tmp")):
            nm = p.name[: -len(DATA_SUFFIX)] if p.name.endswith(DATA_SUFFIX) else None
            if nm is None or nm not in self._entries:
                try:
                    p.unlink()
                except OSError:
                    pass
        self._evict()

    def _add(self, e: Entry) -> None:
        self._entries[e.name] = e
        self._by_key.setdefault(e.key, set()).add(e.name)

    def _drop(self, name: str) -> None:
        e = self._entries.pop(name, None)
        if e is not None:
            s = self._by_key.get(e.key)
            if s is not None:
                s.discard(name)
                if not s:
                    del self._by_key[e.key]
        self._remove_files(name)

    def _remove_files(self, name: str) -> None:
        for suf in (META_SUFFIX, DATA_SUFFIX):
            try:
                (self.dir / f"{name}{suf}").unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass

    def _evict(self) -> None:
        with self._lock:
            total = sum(e.size for e in self._entries.values())
            if total <= self.max_bytes and len(self._entries) <= self.max_entries:
                return
            for e in sorted(self._entries.values(), key=lambda x: x.atime):
                if total <= self.max_bytes and len(self._entries) <= self.max_entries:
                    break
                total -= e.size
                self._drop(e.name)
                self.stats["evicted"] += 1

    # ------------------------------------------------------------------ okuma / yazma
    def entries(self, key: str) -> list[Entry]:
        with self._lock:
            return [self._entries[n] for n in self._by_key.get(key, ())]

    def candidates(self, key: str, variant: dict, satisfies: Callable[[dict, dict], bool],
                   rank: Callable[[dict], tuple]) -> list[Entry]:
        """İsteği karşılayan kayıtlar, en iyiden kötüye."""
        ok = [e for e in self.entries(key) if _safe(satisfies, e.variant, variant)]
        return sorted(ok, key=lambda e: (rank(e.variant), -e.created))

    def read(self, e: Entry) -> dict | None:
        """Kaydın sonucunu okur (bozuksa siler, None)."""
        path = self.dir / f"{e.name}{DATA_SUFFIX}"
        try:
            with gzip.open(path, "rb") as f:
                obj = orjson.loads(f.read())
            if not isinstance(obj, dict) or obj.get("key") != e.key or not isinstance(obj.get("result"), dict):
                raise ValueError("içerik anahtarla uyuşmuyor")
        except Exception:
            with self._lock:
                self.stats["corrupt"] += 1
                self._drop(e.name)
            return None
        now = time.time()
        try:
            os.utime(self.dir / f"{e.name}{META_SUFFIX}", (now, now))
        except OSError:
            pass
        with self._lock:
            e.atime = now
        return obj["result"]

    def lookup(self, key: str, variant: dict, satisfies: Callable[[dict, dict], bool],
               rank: Callable[[dict], tuple]) -> tuple[dict, Entry] | None:
        for e in self.candidates(key, variant, satisfies, rank):
            res = self.read(e)
            if res is not None:
                with self._lock:
                    self.stats["hits"] += 1
                return res, e
        with self._lock:
            self.stats["misses"] += 1
        return None

    def put(self, key: str, variant: dict, result: dict, *, job_type: str = "") -> Entry | None:
        """Sonucu yazar (aynı anahtar + değişke varsa üzerine yazar)."""
        name = _name(key, variant)
        try:
            body = orjson.dumps({"key": key, "variant": variant, "result": result},
                                option=orjson.OPT_SERIALIZE_NUMPY | orjson.OPT_NON_STR_KEYS)
            data = gzip.compress(body, compresslevel=5)
            now = time.time()
            meta = {"key": key, "variant": variant, "engine": self.engine, "job_type": job_type, "size": len(data),
                    "created": now, "raw_size": len(body)}
            with self._lock:
                _atomic_write(self.dir / f"{name}{DATA_SUFFIX}", data)
                _atomic_write(self.dir / f"{name}{META_SUFFIX}", orjson.dumps(meta))
                e = Entry(name=name, key=key, variant=variant, engine=self.engine, job_type=job_type, size=len(data),
                          created=now, atime=now)
                self._drop_index_only(name)
                self._add(e)
                self.stats["puts"] += 1
            self._evict()
            return e
        except Exception as ex:
            log.warning("önbelleğe yazılamadı: %s", ex)
            return None

    def _drop_index_only(self, name: str) -> None:
        e = self._entries.pop(name, None)
        if e is not None:
            s = self._by_key.get(e.key)
            if s is not None:
                s.discard(name)
                if not s:
                    del self._by_key[e.key]

    def has(self, key: str, variant: dict) -> bool:
        with self._lock:
            return _name(key, variant) in self._entries

    def clear(self) -> None:
        with self._lock:
            for n in list(self._entries):
                self._drop(n)

    def info(self) -> dict:
        with self._lock:
            return {"dir": str(self.dir), "entries": len(self._entries),
                    "bytes": sum(e.size for e in self._entries.values()), "max_bytes": self.max_bytes,
                    "max_entries": self.max_entries, "engine": self.engine, **self.stats}

    def names(self) -> Iterable[str]:
        with self._lock:
            return list(self._entries)


def _safe(fn: Callable[[dict, dict], bool], a: dict, b: dict) -> bool:
    try:
        return bool(fn(a, b))
    except Exception:
        return False
