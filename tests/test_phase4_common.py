"""Faz 4 — ortak yardımcılar (diğer test_phase4_* dosyaları kullanır) ve disk önbelleğinin birim testleri.

Çalıştırma (repo kökünden):  .venv/bin/python -m pytest -q tests/test_phase4_common.py
"""
from __future__ import annotations

import gzip
import os
import subprocess
import sys
import time
from pathlib import Path

import orjson

from backend.app import task_solve, tasks
from backend.app.cache import ResultCache
from backend.app.config import engine_version

ROOT = Path(__file__).resolve().parents[1]
FINAL = ("done", "failed", "cancelled")
BASE_URL = "http://127.0.0.1:8000"          # Host başlığı beyaz listede olmalı (main.Guard)


def n_precomputed() -> int:
    """Önbelleğe tohumlanan önceden çözülmüş solve sonucu sayısı (3 varsayılan + senaryo dosyaları)."""
    return len(task_solve.precomputed_files())


# ---------------------------------------------------------------------- yardımcılar (süreç ve iş)
def pid_alive(pid: int) -> bool:
    """Süreç yaşıyor mu (zombi sayılmaz)."""
    if sys.platform == "win32":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("State:"):
                    return "Z" not in line.split()[1]
    except OSError:
        return False
    return True


def children_map() -> dict[int, list[int]]:
    """ebeveyn → çocuklar (Linux /proc; Windows'ta CIM)."""
    out: dict[int, list[int]] = {}
    if sys.platform == "win32":
        txt = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "Get-CimInstance Win32_Process | ForEach-Object { \"$($_.ProcessId) $($_.ParentProcessId)\" }"],
                             capture_output=True, text=True).stdout
        for ln in txt.splitlines():
            parts = ln.split()
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                out.setdefault(int(parts[1]), []).append(int(parts[0]))
        return out
    for d in Path("/proc").iterdir():
        if not d.name.isdigit():
            continue
        try:
            stat = (d / "stat").read_text()
        except OSError:
            continue
        ppid = int(stat.rsplit(")", 1)[1].split()[1])
        out.setdefault(ppid, []).append(int(d.name))
    return out


def descendants(pid: int) -> list[int]:
    cm = children_map()
    res, stack = [], [pid]
    while stack:
        p = stack.pop()
        for c in cm.get(p, []):
            if pid_alive(c):
                res.append(c)
                stack.append(c)
    return res


def kill_hard(pid: int) -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
    else:
        import signal
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def wait_for(cond, timeout: float, period: float = 0.1):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        v = cond()
        if v:
            return v
        time.sleep(period)
    return cond()


def wait_ready(get_json, timeout: float = 120.0) -> dict:
    """get_json('/api/health') çözücü hazır diyene dek bekler."""
    h = wait_for(lambda: (lambda d: d if d.get("solver_ready") else None)(get_json("/api/health")), timeout, 0.2)
    assert h, "çözücü işçisi zamanında hazır olmadı"
    return h


def wait_final(get_json, job_id: str, timeout: float = 120.0) -> dict:
    s = wait_for(lambda: (lambda d: d if d["state"] in FINAL else None)(get_json(f"/api/jobs/{job_id}")), timeout, 0.1)
    assert s, f"iş {job_id} {timeout} sn içinde bitmedi"
    return s


def parse_sse(lines) -> list[dict]:
    """SSE satırları → [{'event', 'data', 'id', 't'}] (t: satırın alındığı an, monotonic)."""
    evs, cur = [], {}
    for line in lines:
        if line == "":
            if cur:
                evs.append(cur)
                cur = {}
            continue
        if line.startswith(":"):
            continue
        k, _, v = line.partition(":")
        v = v[1:] if v.startswith(" ") else v
        if k == "data":
            cur["data"] = orjson.loads(v)
            cur["t"] = time.monotonic()
        elif k in ("event", "id"):
            cur[k] = v
    if cur:
        evs.append(cur)
    return evs


# ---------------------------------------------------------------------- önbellek birim testleri
def _cache(tmp_path, **kw) -> ResultCache:
    return ResultCache(tmp_path, max_bytes=kw.get("max_bytes", 10 ** 8), max_entries=kw.get("max_entries", 100),
                       engine=kw.get("engine", engine_version()))


def test_time_bucket_rounds_down():
    assert tasks.time_bucket(15) == 15 and tasks.time_bucket(16.9) == 15 and tasks.time_bucket(20) == 20
    assert tasks.time_bucket(0.5) == 1 and tasks.time_bucket(1800) == 1800 and tasks.time_bucket(5000) == 3600


def test_solve_cache_dominance_rules():
    s = task_solve.satisfies
    pre = {"tl": 1800.0, "gap": 1e-4, "duals": True, "ach": 6.2e-4, "status": "sure_doldu"}
    assert s(pre, {"tl": 15.0, "gap": 0.01, "duals": True})          # daha uzun ve daha sıkı çözülmüş kayıt
    assert not s(pre, {"tl": 3600.0, "gap": 1e-5, "duals": False})   # istenen çaba daha büyük
    live = {"tl": 15.0, "gap": 0.01, "duals": False, "ach": 0.004, "status": "optimal"}
    assert not s(live, {"tl": 15.0, "gap": 0.01, "duals": True})     # dual değerler yok
    assert s(live, {"tl": 60.0, "gap": 0.005, "duals": False})       # kanıtlı olarak istenen gap içinde
    assert not s(live, {"tl": 60.0, "gap": 0.003, "duals": False})
    assert task_solve.rank(pre) < task_solve.rank(live)              # daha küçük ulaşılan gap önce


def test_cache_roundtrip_lru_and_corruption(tmp_path):
    c = _cache(tmp_path, max_entries=3)
    for i in range(3):
        c.put(f"k{i}", {"tl": 15.0}, {"ok": True, "i": i})
        time.sleep(0.02)
    hit = c.lookup("k0", {"tl": 15.0}, task_solve.satisfies, task_solve.rank)
    assert hit is not None and hit[0]["i"] == 0                       # isabet LRU zamanını tazeler
    time.sleep(0.02)
    c.put("k3", {"tl": 15.0}, {"ok": True, "i": 3})
    names = {e.key for e in (c._entries.values())}
    assert names == {"k0", "k2", "k3"}, names                         # en az yakın zamanda kullanılan (k1) atıldı
    assert c.info()["evicted"] == 1
    # bozuk veri dosyası: yok sayılır ve silinir
    e = c.entries("k2")[0]
    (tmp_path / f"{e.name}.json.gz").write_bytes(b"bozuk")
    assert c.lookup("k2", {"tl": 15.0}, task_solve.satisfies, task_solve.rank) is None
    assert not c.entries("k2") and not (tmp_path / f"{e.name}.json.gz").exists()
    # gzip'i sağlam ama içeriği başka anahtara ait dosya
    e3 = c.entries("k3")[0]
    (tmp_path / f"{e3.name}.json.gz").write_bytes(gzip.compress(orjson.dumps({"key": "baska", "result": {}})))
    assert c.lookup("k3", {"tl": 15.0}, task_solve.satisfies, task_solve.rank) is None
    # bozuk üst veri ve yetim dosyalar açılışta temizlenir; başka motor sürümünün kayıtları silinir
    (tmp_path / "deadbeef.meta.json").write_text("{bozuk")
    (tmp_path / "cafebabe.json.gz").write_bytes(b"yetim")
    c2 = _cache(tmp_path)
    assert {e.key for e in c2._entries.values()} == {"k0"}
    assert not (tmp_path / "deadbeef.meta.json").exists() and not (tmp_path / "cafebabe.json.gz").exists()
    c3 = _cache(tmp_path, engine="baska-surum")
    assert c3.info()["entries"] == 0 and c3.info()["stale_removed"] == 1


def test_cache_size_limit(tmp_path):
    c = _cache(tmp_path, max_bytes=3000)
    import random
    rnd = random.Random(1)
    for i in range(6):                                               # sıkıştırılamaz ≈1 KB'lık sonuçlar
        c.put(f"b{i}", {}, {"ok": True, "pad": "".join(rnd.choice("abcdefghijklmnop") for _ in range(1500))})
    info = c.info()
    assert info["bytes"] <= 3000 and info["evicted"] >= 1 and c.entries("b5")


def test_seed_precomputed_defaults(tmp_path):
    c = _cache(tmp_path)
    n = task_solve.seed(c)
    assert n == n_precomputed() >= 3                                 # 2, 3, 5 km varsayılanlar + senaryolar
    assert task_solve.seed(c) == 0                                   # ikinci kez eklenmez
    from backend.model.params import Params
    for km in (2, 3, 5):
        key = task_solve.solve_key(Params.load({"grid.cell_km": km}), task_solve.NO_LOCKS)
        hit = c.lookup(key, {"tl": 15.0, "gap": 0.01, "duals": True}, task_solve.satisfies, task_solve.rank)
        assert hit is not None, km
        res, entry = hit
        pre = orjson.loads((ROOT / "precomputed" / f"default_{km}km.json").read_bytes())
        assert res["bounds"]["ub_tl"] == pre["bounds"]["ub_tl"] and entry.variant["ach"] == pre["bounds"]["gap"]


def test_registry_discovers_task_modules():
    reg = tasks.load_all()
    assert {"solve", "evaluate", "bounds"} <= set(reg)
    for jt in reg.values():
        assert jt.description_tr and jt.request_model is not None
