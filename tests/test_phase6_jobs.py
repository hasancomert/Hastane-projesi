"""Faz 6 — açıklama iş türleri GERÇEK çözücü işçisiyle (TestClient + spawn ile başlayan işçi, 5 km, kısa süreler):
explain / sweep / pareto / sensitivity yaşam döngüsü (SSE ilerlemesi, sonuç şeması), iptal (kısmi sonuç), önceden
hesaplanmış açıklamaların kuyruğa girmeden dönmesi, doğrulama hataları, GET /api/literature ve önceden hesaplanmış
dosyaların beyaz listesi.

Çalıştırma (repo kökünden):  .venv/bin/python -m pytest -q tests/test_phase6_jobs.py
"""
from __future__ import annotations

import time

import orjson
import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app

from .test_phase4_common import BASE_URL, ROOT, parse_sse, pid_alive, wait_final, wait_for, wait_ready

PRE5 = orjson.loads((ROOT / "precomputed" / "default_5km.json").read_bytes())
HS5 = [{"cell_id": h["cell_id"], "class": h["class"], "beds": h["beds"]} for h in PRE5["hospitals"]]
BB5 = {"ub_tl": PRE5["bounds"]["ub_tl"], "lb_tl": PRE5["bounds"]["lb_tl"]}
LIVE = {"cost.fixed_per_hospital": 300_000_000}          # varsayılandan farklı: önceden hesaplanmış yol kullanılmaz
HAS_EXPLAIN5 = (ROOT / "precomputed" / "explain_5km.json").exists()
HAS_PARETO5 = (ROOT / "precomputed" / "pareto_5km.json").exists()


@pytest.fixture(scope="module")
def ctx(tmp_path_factory):
    s = Settings(cache_dir=tmp_path_factory.mktemp("cache6"), start_worker=True)
    app = create_app(s)
    with TestClient(app, base_url=BASE_URL) as c:
        get = lambda p: c.get(p).json()
        h = wait_ready(get)
        yield {"c": c, "get": get, "app": app, "health": h}
    m = app.state.manager
    pids = [e["pid"] for e in m.restart_log] + ([m.worker.pid] if m.worker is not None and m.worker.pid else [])
    for pid in pids:
        assert wait_for(lambda pid=pid: not pid_alive(pid), 10.0), f"kapanıştan sonra süreç kaldı: {pid}"


def _events(c, jid):
    with c.stream("GET", f"/api/jobs/{jid}/events") as s:
        return parse_sse(s.iter_lines())


def test_meta_literature_and_whitelist(ctx):
    c, get = ctx["c"], ctx["get"]
    m = get("/api/meta")
    for n in ("explain", "sweep", "pareto", "sensitivity"):
        assert m["job_types"][n]["available"] is True, n
    ex = m["explain"]
    assert len(ex["tornado_params"]) == 16 and ex["pareto_factors"][0] == 0.25 and 1.0 in ex["pareto_factors"]
    assert all(r["label_tr"] for r in ex["tornado_params"])
    lit = get("/api/literature")
    assert lit["n"] == len(lit["literature"]) >= 5
    assert any(x["id"] == "hakimi1964" and x["doi"] == "10.1287/opre.12.3.450" for x in lit["literature"])
    assert c.get("/api/precomputed/explain_x_9km").status_code == 404
    assert c.get("/api/precomputed/explain..%2f..%2fREADME").status_code == 404
    if HAS_EXPLAIN5:
        r = c.get("/api/precomputed/explain_5km")
        assert r.status_code == 200 and r.json()["grid_km"] == 5
        assert "explain_5km" in next(g for g in m["grids"] if g["km"] == 5)["precomputed_explanations"]
    # Pareto/tornado varsayılan sonuçları açılışta önbelleğe tohumlanır
    seeded = ctx["health"]["seeded"]
    assert seeded["explain"] == 0 and seeded["sweep"] == 0
    if HAS_PARETO5:
        assert seeded["pareto"] >= 1


def test_validation_errors(ctx):
    c = ctx["c"]
    r = c.post("/api/sensitivity", json={"grid_km": 5, "keys": ["yok.boyle"]})
    assert r.status_code == 422 and r.json()["errors"][0]["type"] == "bilinmeyen_parametre"
    r = c.post("/api/explain", json={"grid_km": 5, "mode": "butce", "hospitals": HS5[:3], "what": ["n_eksi_1"]})
    assert r.status_code == 422 and r.json()["errors"][0]["type"] == "mod_uyumsuz"
    r = c.post("/api/explain", json={"grid_km": 5, "hospitals": HS5[:3], "what": ["hastane_kapat"]})
    assert r.status_code == 422 and r.json()["errors"][0]["type"] == "eksik"
    r = c.post("/api/sweep", json={"grid_km": 5, "hospitals": HS5, "p_values": list(range(1, 80))})
    assert r.status_code == 422
    r = c.post("/api/pareto", json={"grid_km": 5, "factors": [0.0, 2.0]})
    assert r.status_code == 422 and r.json()["errors"][0]["type"] == "aralik_disi"
    r = c.post("/api/jobs/pareto", json={"grid_km": 5, "bilinmeyen": 1})
    assert r.status_code == 422


def test_explain_lifecycle_live(ctx):
    c = ctx["c"]
    cell = HS5[20]["cell_id"]
    body = {"grid_km": 5, "overrides": LIVE, "hospitals": HS5, "close": [cell, HS5[0]["cell_id"]],
            "what": ["hastane_kapat", "n_arti_1", "boyut", "golge_fiyat", "model"], "time_limit": 4, "use_cache": False}
    r = c.post("/api/explain", json=body)
    assert r.status_code == 202, r.text
    jid = r.json()["job_id"]
    evs = _events(c, jid)
    prog = [e["data"] for e in evs if e["event"] == "progress"]
    assert prog and prog[-1]["phase"] == "bitti" and prog[-1]["done"] == prog[-1]["total"] == 6
    assert any(p.get("last", {}).get("kind") == "hastane_kapat" for p in prog)
    final = evs[-1]["data"]
    assert final["state"] == "done", final
    res = final["result"]
    assert res["ok"] and res["status"] == "tamam" and res["base"]["n_hospitals"] == 69
    items = {(i["kind"], i.get("cell_id")): i for i in res["items"]}
    cl = items[("hastane_kapat", cell)]
    assert cl["feasible"] is True and cl["delta_tl"] is not None and cl["absorbers"] and cl["message_tr"]
    assert "_solution" not in cl
    forced = items[("hastane_kapat", HS5[0]["cell_id"])]
    assert forced["feasible"] is False and forced["reason"] == "R"
    n1 = items[("n_arti_1", None)]
    assert n1["ok"] and n1["p"] == 70 and n1["source"] == "canli" and n1["lb_tl"] is not None
    assert n1["delta_tl"] == n1["fark_tl"] and n1["fark_gap_icinde"] in (True, False)
    assert len(items[("boyut", None)]["hospitals"]) == 69
    assert len(items[("golge_fiyat", None)]["marginal_cost_tl"]) == res_demand_cells()
    assert items[("model", None)]["reasons"]
    assert res["closure_summary"]["n"] == 2
    # aynı istek önbellekten
    r2 = c.post("/api/explain", json={**body, "use_cache": True})
    assert r2.status_code == 200 and r2.json()["from_cache"] is True


def res_demand_cells() -> int:
    return len(PRE5["layers"]["demand"]["cell_id"])


@pytest.mark.skipif(not HAS_EXPLAIN5, reason="precomputed/explain_5km.json yok")
def test_explain_default_from_precomputed_instantly(ctx):
    """Varsayılan parametreler + önceden çözülmüş temel çözüm: yanıt kuyruğa girmeden (200) gelir; N ± 1 noktaları
    precomputed/p_sweep_5km.json'un uzun çözülmüş noktalarıyla aynıdır."""
    c = ctx["c"]
    t0 = time.perf_counter()
    r = c.post("/api/explain", json={"grid_km": 5, "hospitals": HS5, "close": [HS5[20]["cell_id"]],
                                     "what": ["hastane_kapat", "n_eksi_1", "n_arti_1"], "base_bounds": BB5})
    assert r.status_code == 200, r.text
    snap = r.json()
    assert snap["from_cache"] and time.perf_counter() - t0 < 2.0
    res = snap["result"]
    assert res["precomputed"]["name"] == "explain_5km"
    sweep = {q["p"]: q for q in orjson.loads((ROOT / "precomputed" / "p_sweep_5km.json").read_bytes())["points"]}
    for it in res["items"]:
        if it["kind"] in ("n_eksi_1", "n_arti_1"):
            q = sweep[it["p"]]
            assert it["source"] == "p_taramasi" and it["ub_tl"] == q["ub_tl"] and it["lb_tl"] == q["lb_tl"]
            assert it["fark_gap_icinde"] == q["fark_gap_icinde"]


def test_sweep_default_points_and_live_point_cancel(ctx):
    c, get = ctx["c"], ctx["get"]
    # varsayılan: dosyadaki p'ler kuyruğa girmeden
    r = c.post("/api/sweep", json={"grid_km": 5, "hospitals": HS5, "p_values": [67, 68, 69, 70, 71],
                                   "base_bounds": BB5})
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    assert res["best_p"] == 69 and [q["p"] for q in res["points"]] == [67, 68, 69, 70, 71]
    assert all(q["fark_gap_icinde"] is False for q in res["points"] if q["p"] != 69)       # 5 km kanıtlı
    # canlı tarama: ilk noktadan sonra iptal → kısmi sonuç, 'durduruldu'
    r = c.post("/api/sweep", json={"grid_km": 5, "overrides": LIVE, "hospitals": HS5, "half_width": 6,
                                   "time_limit": 4, "use_cache": False})
    assert r.status_code == 202, r.text
    jid = r.json()["job_id"]
    assert wait_for(lambda: ((get(f"/api/jobs/{jid}").get("progress") or {}).get("done") or 0) >= 1, 90, 0.2)
    c.post(f"/api/jobs/{jid}/cancel")
    s = wait_final(get, jid, 60)
    assert s["state"] == "cancelled" and s["result"]["status"] == "durduruldu"
    pts = [q for q in s["result"]["points"] if q.get("ok")]
    assert 1 <= len(pts) < 13 and all("fark_gap_icinde" in q for q in pts)


def test_pareto_and_sensitivity_lifecycle(ctx):
    c, get = ctx["c"], ctx["get"]
    r = c.post("/api/pareto", json={"grid_km": 5, "overrides": LIVE, "factors": [0.5, 2.0], "hospitals": HS5,
                                    "time_limit": 3, "use_cache": False})
    assert r.status_code == 202, r.text
    evs = _events(c, r.json()["job_id"])
    pts_ev = [e["data"] for e in evs if e["event"] == "progress" and e["data"].get("point")]
    assert [p["done"] for p in pts_ev] == [1, 2, 3]
    res = evs[-1]["data"]["result"]
    assert evs[-1]["data"]["state"] == "done" and [q["factor"] for q in res["points"]] == [0.5, 1.0, 2.0]
    cur = res["points"][1]
    assert cur["source"] == "mevcut_cozum" and cur["ok"]
    for q in res["points"]:
        assert q["total_tl"] == pytest.approx(q["opening_tl"] + q["access_tl"])
    r = c.post("/api/sensitivity", json={"grid_km": 5, "overrides": LIVE, "keys": ["cost.fixed_per_hospital"],
                                         "hospitals": HS5, "time_limit": 3, "use_cache": False})
    assert r.status_code == 202, r.text
    s = wait_final(get, r.json()["job_id"], 120)
    assert s["state"] == "done", s
    rows = s["result"]["rows"]
    assert len(rows) == 1 and rows[0]["low"]["factor"] == pytest.approx(0.8) and rows[0]["high"]["factor"] == pytest.approx(1.2)
    assert rows[0]["high"]["ok"] and rows[0]["low"]["ok"] and s["result"]["summary_tr"]


@pytest.mark.skipif(not HAS_PARETO5, reason="precomputed/pareto_5km.json yok")
def test_pareto_default_seeded(ctx):
    r = ctx["c"].post("/api/pareto", json={"grid_km": 5})
    assert r.status_code == 200 and r.json()["from_cache"] is True
    assert r.json()["result"]["precomputed"]["name"] == "pareto_5km"


@pytest.mark.parametrize("kind,body", [
    ("explain", {"what": ["hastane_kapat"], "close_all": True, "exact": True}),
    ("pareto", {"factors": [0.25, 0.5, 2.0, 4.0], "time_limit": 6}),
    ("sensitivity", {"keys": ["cost.fixed_per_hospital", "cost.core_area_m2", "access.speed_kmh"], "time_limit": 6}),
])
def test_cancel_returns_partial_result(ctx, kind, body):
    """Her açıklama işi iptali izler: ilk öğe/nokta bittikten sonra iptal → 'cancelled', sonuç 'durduruldu' ve o ana
    dek tamamlananlar."""
    c, get = ctx["c"], ctx["get"]
    r = c.post(f"/api/{kind}", json={"grid_km": 5, "overrides": LIVE, "hospitals": HS5, "use_cache": False, **body})
    assert r.status_code == 202, r.text
    jid = r.json()["job_id"]
    assert wait_for(lambda: ((get(f"/api/jobs/{jid}").get("progress") or {}).get("done") or 0) >= 1, 120, 0.2), \
        get(f"/api/jobs/{jid}")
    t0 = time.monotonic()
    c.post(f"/api/jobs/{jid}/cancel")
    s = wait_final(get, jid, 60)
    assert s["state"] == "cancelled" and time.monotonic() - t0 < 30
    res = s["result"]
    assert res["status"] == "durduruldu"
    done = res.get("items") or res.get("points") or [x for row in res.get("rows", []) for x in (row["low"], row["high"]) if x]
    assert len(done) >= 1
