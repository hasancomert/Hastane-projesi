"""Faz 4 — API üzerinden uçtan uca gecikme ölçümü (isteğe bağlı; normal test takımında atlanır).

Gerçek sunucu (python -m backend.run) başlatılır ve şunlar ölçülür: sunucu açılışı (sağlık yanıtı, çözücü hazır),
3 km çözüm (önbelleksiz; soğuk = sunucu açıldıktan sonraki ilk çözüm — çift sıradaki sunucularda varsayılan
parametrelerle, tek sıradakilerde cost.fixed_per_hospital = 300 milyon TL ile —, ılık = aynı istek tekrar, ılık +
parametre değişikliği), önbellekten varsayılan 3 km isteği, 3 km manuel değerlendirme, SSE olay aralıkları; ilk
sunucuda ayrıca süre sınırı verilmeden 2 km çözümü (ızgaranın önerilen süresi, 60 sn). Sonuç JSON olarak yazdırılır
ve HASTANE_LATENCY_OUT verilmişse o dosyaya yazılır.

Çalıştırma (repo kökünden):
  HASTANE_LATENCY=1 [HASTANE_LATENCY_SERVERS=6] .venv/bin/python -m pytest -q -s tests/test_phase4_latency.py
"""
from __future__ import annotations

import os
import statistics
import time

import orjson
import pytest

from .test_phase4_common import ROOT, parse_sse, wait_final, wait_ready
from .test_phase4_server import Server

pytestmark = pytest.mark.skipif(os.environ.get("HASTANE_LATENCY") != "1",
                                reason="ölçüm testi: HASTANE_LATENCY=1 ile çalıştırın")
REPEATS_SERVER = int(os.environ.get("HASTANE_LATENCY_SERVERS", "2"))
PRE3 = orjson.loads((ROOT / "precomputed" / "default_3km.json").read_bytes())


def _pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))]


def _solve(srv: Server, body: dict) -> dict:
    t0 = time.monotonic()
    r = srv.http.post("/api/solve", json=body)
    t_post = time.monotonic() - t0
    assert r.status_code == 202, r.text
    jid = r.json()["job_id"]
    with srv.http.stream("GET", f"/api/jobs/{jid}/events", timeout=120) as s:
        evs = parse_sse(s.iter_lines())
    wall = evs[-1]["t"] - t0
    res = evs[-1]["data"]["result"]
    prog = [e for e in evs if e["event"] == "progress"]
    gaps = [b["t"] - a["t"] for a, b in zip(evs, evs[1:])]
    first_lb = next((e["t"] - t0 for e in prog if e["data"].get("lb") is not None), None)
    to_1pct = next((e["t"] - t0 for e in prog if e["data"].get("gap") is not None and e["data"]["gap"] <= 0.01), None)
    att = ((res.get("core_mip") or {}).get("attempts") or [{}])[0]
    return {"post_s": round(t_post, 4), "wall_s": round(wall, 3), "engine_total_s": round(res["times"]["total_s"], 3),
            "time_limit_s": res.get("time_limit_s"), "seeds_adopted": [x["adopted"] for x in res.get("seeds") or []],
            "core_spawn_s": att.get("spawn_s"), "core_from_spare": att.get("from_spare"),
            "n_hospitals": res["kpis"]["n_hospitals"],
            "first_progress_s": round(prog[0]["t"] - t0, 3), "first_lb_s": first_lb and round(first_lb, 3),
            "to_gap_1pct_s": to_1pct and round(to_1pct, 3), "status": res["status"],
            "gap": res["bounds"]["gap"], "ub_tl": res["bounds"]["ub_tl"], "n_events": len(evs),
            "n_progress": len(prog), "event_gap_median_s": round(statistics.median(gaps), 3),
            "event_gap_p95_s": round(_pct(gaps, 0.95), 3), "event_gap_max_s": round(max(gaps), 3),
            "heartbeats": sum(e["event"] == "heartbeat" for e in evs)}


def _evaluate(srv: Server, hospitals: list[dict]) -> dict:
    t0 = time.monotonic()
    r = srv.http.post("/api/evaluate", json={"grid_km": 3, "hospitals": hospitals})
    post = time.monotonic() - t0
    if r.status_code == 200:
        return {"post_s": round(post, 4), "wall_s": round(post, 4), "from_cache": True}
    s = wait_final(srv.get, r.json()["job_id"], 120)
    wall = time.monotonic() - t0
    res = s["result"]
    return {"post_s": round(post, 4), "wall_s": round(wall, 3), "from_cache": False, "status": res["status"],
            "instance_s": round(res["times"]["instance_s"], 3), "evaluate_s": round(res["times"]["evaluate_s"], 3),
            "duals_s": round(res["times"].get("duals_s", 0.0), 3), "engine_total_s": round(res["times"]["total_s"], 3)}


def test_latency_through_api(tmp_path):
    import platform
    out = {"machine_note": f"geliştirme kabı ({platform.platform()}, {os.cpu_count()} çekirdek, Python "
                           f"{platform.python_version()}); dizüstü ölçülmedi", "servers": []}
    hs = [{"cell_id": h["cell_id"]} for h in PRE3["hospitals"]]
    for k in range(REPEATS_SERVER):
        d = tmp_path / f"s{k}"
        d.mkdir()
        t0 = time.monotonic()
        srv = Server(d)
        try:
            ok_http = None
            while ok_http is None and time.monotonic() - t0 < 120:
                try:
                    srv.http.get("/api/health")
                    ok_http = time.monotonic() - t0
                except Exception:
                    time.sleep(0.05)
            wait_ready(srv.get, 120)
            ready = time.monotonic() - t0
            body = {"grid_km": 3, "use_cache": False}
            param = {**body, "overrides": {"cost.fixed_per_hospital": 3e8}}
            first_is_param = k % 2 == 1
            rec = {"startup_http_s": round(ok_http, 3), "startup_solver_ready_s": round(ready, 3),
                   "cold_first_request": "param_change" if first_is_param else "default"}
            rec["solve_3km_cold"] = _solve(srv, param if first_is_param else body)
            rec["solve_3km_warm"] = [_solve(srv, body) for _ in range(2)]
            rec["solve_3km_warm_param_change"] = _solve(srv, {**body, "overrides": {"cost.fixed_per_hospital": 3.5e8}})
            if k == 0:
                # süre sınırı yok → ızgaranın önerilen süresi (2 km 60 sn); 2 km yapısı işçide boşken kurulmuş olmalı
                rec["solve_2km_param_change_no_time_limit"] = _solve(srv, {"grid_km": 2, "use_cache": False,
                                                                          "overrides": {"cost.fixed_per_hospital": 3e8}})
            t = time.monotonic()
            r = srv.http.post("/api/solve", json={"grid_km": 3})
            rec["solve_3km_cached_post_s"] = round(time.monotonic() - t, 4)
            assert r.status_code == 200 and r.json()["from_cache"]
            # manuel düzenleme örneği: bir hastanenin sınıfı elle sabit (karışık yol), diğerleri serbest
            hs2 = [{**hs[0], "class": PRE3["hospitals"][0]["class"]}] + hs[1:]
            rec["evaluate_3km_first"] = _evaluate(srv, hs)
            rec["evaluate_3km_one_class_fixed"] = _evaluate(srv, hs2)
            rec["evaluate_3km_cached"] = _evaluate(srv, hs2)
            out["servers"].append(rec)
        finally:
            srv.close()
    txt = orjson.dumps(out, option=orjson.OPT_INDENT_2).decode()
    print(txt)
    if os.environ.get("HASTANE_LATENCY_OUT"):
        with open(os.environ["HASTANE_LATENCY_OUT"], "w", encoding="utf-8") as f:
            f.write(txt)
