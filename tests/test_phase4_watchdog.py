"""Faz 4 — bekçi: iş beklenen süreyi aşınca durdurma (en iyi çözüm yine gelir) ve işçi yanıt vermeyince (kalp atışı
kesilince) yeniden başlatma; ardından yeni işin çalışması ve artık süreç kalmaması.

Kısa sınırlarla: watchdog_extra_s negatif verilerek 60 sn'lik çözümün beklenen süresi ≈4 sn'ye indirilir; kalp atışı
zaman aşımı 3 sn. "Yanıt vermeyen işçi" POSIX'te SIGSTOP ile taklit edilir (yalnız testte; uygulama sinyal kullanmaz).

Çalıştırma (repo kökünden):  .venv/bin/python -m pytest -q tests/test_phase4_watchdog.py
"""
from __future__ import annotations

import os
import sys
import time

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app

from .test_phase4_common import BASE_URL, descendants, pid_alive, wait_final, wait_for, wait_ready

LONG = {"grid_km": 5, "time_limit": 60, "gap": 1e-6, "use_cache": False}


@pytest.fixture(scope="module")
def ctx(tmp_path_factory):
    # çözüm işinin bekçi süresi = 1,1·60 + 20 + extra = 4 sn
    s = Settings(cache_dir=tmp_path_factory.mktemp("cache"), watchdog_extra_s=-82.0, cancel_grace_s=3.0,
                 heartbeat_timeout_s=3.0, seed_precomputed=False)
    app = create_app(s)
    with TestClient(app, base_url=BASE_URL) as c:
        get = lambda p: c.get(p).json()
        wait_ready(get)
        yield {"c": c, "get": get, "app": app}
    m = app.state.manager
    for pid in [e["pid"] for e in m.restart_log] + [m.worker.pid]:
        assert wait_for(lambda pid=pid: not pid_alive(pid), 10.0), pid


def test_deadline_stops_job_with_best_solution(ctx):
    c, get = ctx["c"], ctx["get"]
    t = time.monotonic()
    jid = c.post("/api/solve", json={**LONG, "overrides": {"demand.safety_margin": 0.08}}).json()["job_id"]
    s = wait_final(get, jid, 30)
    assert 3.0 < time.monotonic() - t < 12.0
    assert s["state"] == "cancelled" and "bekçi" in s["error"]["message_tr"]
    assert s["result"]["status"] == "durduruldu" and s["result"]["check"]["ok"]
    assert get("/api/health")["restarts"] == 0


@pytest.mark.skipif(sys.platform == "win32", reason="SIGSTOP yalnız POSIX'te")
def test_unresponsive_worker_is_restarted(ctx):
    import signal
    c, get = ctx["c"], ctx["get"]
    old = get("/api/health")["worker"]["pid"]
    jid = c.post("/api/solve", json={**LONG, "overrides": {"demand.safety_margin": 0.09}}).json()["job_id"]
    assert wait_for(lambda: get(f"/api/jobs/{jid}")["n_progress"] >= 2, 30, 0.05)
    kids = descendants(old)
    os.kill(old, signal.SIGSTOP)                          # işçi donar: kalp atışı ve iptal yanıtı gelmez
    try:
        s = wait_final(get, jid, 30)
    finally:
        try:
            os.kill(old, signal.SIGCONT)
        except ProcessLookupError:
            pass
    assert s["state"] == "failed" and "yeniden başlatıldı" in s["error"]["message_tr"]
    h = wait_ready(get)
    assert h["restarts"] == 1 and h["worker"]["pid"] != old
    reason = h["restart_log"][-1]["reason_tr"]
    assert "yanıt vermedi" in reason, reason
    for p in kids + [old]:
        assert wait_for(lambda p=p: not pid_alive(p), 5.0), p
    # yeni işçide iş çalışır (bekçi süresi 1,1·250 + 20 − 82 = 213 sn; 5 km çözümü hedef gap'e birkaç saniyede ulaşır)
    s2 = wait_final(get, c.post("/api/solve", json={"grid_km": 5, "time_limit": 250, "use_cache": False}).json()["job_id"],
                    120)
    assert s2["state"] == "done" and s2["result"]["ok"] and s2["result"]["status"] == "optimal"
