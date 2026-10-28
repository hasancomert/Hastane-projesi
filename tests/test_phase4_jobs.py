"""Faz 4 — iş sistemi GERÇEK çözücü işçisiyle (TestClient + spawn ile başlayan işçi süreci, 5 km, kısa süre sınırları):
çözüm yaşam döngüsü ve SSE olayları, önbellek isabeti, çalışırken iptal, 'en son istek kazanır', manuel değerlendirme
(maliyet, dual değerler, optimumla karşılaştırma), sınır kartı işi, işçi çökmesinden kurtulma ve kapanışta artık süreç
kalmaması.

Çalıştırma (repo kökünden):  .venv/bin/python -m pytest -q tests/test_phase4_jobs.py
"""
from __future__ import annotations

import time

import orjson
import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app

from .test_phase4_common import BASE_URL, ROOT, n_precomputed, descendants, kill_hard, parse_sse, pid_alive, wait_final, wait_for, wait_ready

PRE5 = orjson.loads((ROOT / "precomputed" / "default_5km.json").read_bytes())
LONG = {"grid_km": 5, "time_limit": 60, "gap": 1e-6, "use_cache": False}    # iptal edilecek uzun çözüm


@pytest.fixture(scope="module")
def ctx(tmp_path_factory):
    s = Settings(cache_dir=tmp_path_factory.mktemp("cache"), start_worker=True)
    app = create_app(s)
    with TestClient(app, base_url=BASE_URL) as c:
        get = lambda p: c.get(p).json()
        h = wait_ready(get)
        yield {"c": c, "get": get, "app": app, "pids": [h["worker"]["pid"]]}
    # sunucu kapandı: işçi ve motorun alt süreçleri kalmamalı
    for pid in ctx_pids(app):
        assert wait_for(lambda pid=pid: not pid_alive(pid), 10.0), f"kapanıştan sonra süreç kaldı: {pid}"


def ctx_pids(app) -> list[int]:
    m = app.state.manager
    pids = [e["pid"] for e in m.restart_log]
    if m.worker is not None and m.worker.pid:
        pids.append(m.worker.pid)
    return pids


def _running(get, jid, min_progress: int = 2, timeout: float = 60.0) -> dict:
    s = wait_for(lambda: (lambda d: d if d["state"] == "running" and d["n_progress"] >= min_progress else None)(
        get(f"/api/jobs/{jid}")), timeout, 0.05)
    assert s, get(f"/api/jobs/{jid}")
    return s


def test_health_with_worker(ctx):
    h = ctx["get"]("/api/health")
    assert h["solver_ready"] and h["worker"]["alive"] and h["restarts"] == 0
    assert h["seeded"]["solve"] == n_precomputed()        # Faz 6 iş türleri de kendi önceden hesaplananlarını ekler


def test_first_live_solve_is_seeded_and_has_layers(ctx):
    """Bulgu 1: işçi ısınmada önceden çözülmüş çözümlerle tohumlanır; sunucu açıldıktan sonraki ilk 3 km canlı çözümü
    (yapıyı değiştirmeyen bir parametre farkıyla) bu tohumdan başlar. Bulgu 6: sonuçta harita katmanları var."""
    c, get = ctx["c"], ctx["get"]
    body = {"grid_km": 3, "use_cache": False, "overrides": {"cost.fixed_per_hospital": 300_000_000}}
    r = c.post("/api/solve", json=body)
    assert r.status_code == 202 and r.json()["info"]["time_limit_s"] == 15.0      # ızgaranın önerilen süresi
    s = wait_final(get, r.json()["job_id"], 60)
    res = s["result"]
    assert s["state"] == "done" and res["ok"] and res["check"]["ok"]
    assert res["seeds"] and res["seeds"][0]["adopted"], res.get("seeds")
    assert res["bounds"]["gap"] <= 0.02
    L = res["layers"]
    assert len(L["access"]["nearest_km"]) == 3111 and len(L["demand"]["cell_id"]) == res["instance"]["I"]
    assert L["rings"]["R_crowfly_km"] < L["rings"]["R_km"] == 30


def test_solve_lifecycle_sse_5km(ctx):
    c = ctx["c"]
    r = c.post("/api/solve", json={"grid_km": 5, "time_limit": 6, "use_cache": False, "client_id": "yasam"})
    assert r.status_code == 202
    snap = r.json()
    assert snap["state"] in ("queued", "running") and snap["from_cache"] is False and "result" not in snap
    jid = snap["job_id"]
    t0 = time.monotonic()
    with c.stream("GET", f"/api/jobs/{jid}/events") as s:
        assert s.headers["content-type"].startswith("text/event-stream")
        evs = parse_sse(s.iter_lines())
    names = [e["event"] for e in evs]
    assert names[0] == "state" and evs[0]["data"]["state"] == "queued" and names[-1] == "result"
    assert [int(e["id"]) for e in evs if "id" in e] == list(range(1, len([e for e in evs if "id" in e]) + 1))
    prog = [e["data"] for e in evs if e["event"] == "progress"]
    assert len(prog) >= 5
    for p in prog:
        assert {"t", "phase", "phase_tr", "lb", "ub", "gap"} <= set(p)
    phases = [p["phase"] for p in prog]
    assert phases[0] == "baslangic" and "lagrange" in phases and phases[-1] == "bitti"
    ubs = [p["ub"] for p in prog if p["ub"] is not None]
    lbs = [p["lb"] for p in prog if p["lb"] is not None]
    assert ubs and lbs and all(b <= a for a, b in zip(ubs, ubs[1:])) and all(b >= a for a, b in zip(lbs, lbs[1:]))
    # olaylar akışla gelir: ilk ilerleme sonuçtan önemli ölçüde önce
    assert evs[-1]["t"] - next(e["t"] for e in evs if e["event"] == "progress") > 1.0
    final = evs[-1]["data"]
    res = final["result"]
    assert final["state"] == "done" and final["from_cache"] is False
    assert res["ok"] and res["check"]["ok"] and res["status"] in ("optimal", "sure_doldu", "gap_ulasilamadi")
    assert res["kpis"]["n_hospitals"] > 0 and res["duals"]["marginal_cost_tl"]
    assert res["bounds"]["lb_tl"] <= res["bounds"]["ub_tl"] and res["bounds"]["gap"] <= 0.02
    assert prog[-1]["ub"] == res["bounds"]["ub_tl"] and prog[-1]["status"] == res["status"]
    assert res["times"]["total_s"] <= 6 + 1.0 and time.monotonic() - t0 < 20
    # aynı iş GET ile; SSE yeniden bağlanma (Last-Event-ID) yalnız sonrakileri verir
    g = ctx["get"](f"/api/jobs/{jid}")
    assert g["state"] == "done" and g["result"]["bounds"]["ub_tl"] == res["bounds"]["ub_tl"]
    assert "result" not in ctx["get"](f"/api/jobs/{jid}?result=false")
    with c.stream("GET", f"/api/jobs/{jid}/events", headers={"Last-Event-ID": str(len(evs) - 2)}) as s:
        again = parse_sse(s.iter_lines())
    assert [e["event"] for e in again] == names[-2:]
    ctx["lifecycle_ub"] = res["bounds"]["ub_tl"]


def test_cache_hit_identical_request(ctx):
    c = ctx["c"]
    t = time.perf_counter()
    r = c.post("/api/solve", json={"grid_km": 5, "time_limit": 6, "client_id": "yasam"})
    dt = time.perf_counter() - t
    assert r.status_code == 200, r.text
    snap = r.json()
    assert snap["from_cache"] and snap["state"] == "done" and dt < 1.0
    # varsayılan 5 km isteği önceden çözülmüş (kanıtlı) çözümü döndürür: aynı problemde daha iyi kayıt önce gelir
    assert snap["result"]["bounds"]["ub_tl"] == PRE5["bounds"]["ub_tl"] <= ctx["lifecycle_ub"]
    # dual değersiz istek de karşılanır; daha sıkı gap ve uzun süre istenirse önbellek kullanılmaz
    with c.stream("GET", f"/api/jobs/{snap['job_id']}/events") as s:
        evs = parse_sse(s.iter_lines())
    assert evs[-1]["event"] == "result" and evs[-1]["data"]["from_cache"] is True
    # kanıtlı optimum (5 km, status optimal, gap 0) her gap isteğini karşılar
    r1 = c.post("/api/solve", json={"grid_km": 5, "time_limit": 3600, "gap": 1e-7})
    assert r1.status_code == 200 and r1.json()["result"]["status"] == "optimal"
    # 3 km önceden çözülmüşü 1.800 sn, fark %0,062: daha uzun süre ve daha sıkı gap istenirse önbellek kullanılmaz
    r2 = c.post("/api/solve", json={"grid_km": 3, "time_limit": 3600, "gap": 1e-4})
    assert r2.status_code == 202
    c.post(f"/api/jobs/{r2.json()['job_id']}/cancel")
    wait_final(ctx["get"], r2.json()["job_id"], 30)
    r2b = c.post("/api/solve", json={"grid_km": 3, "time_limit": 15, "gap": 0.01})
    assert r2b.status_code == 200 and r2b.json()["result"]["time_limit_s"] == 1800
    # farklı parametre: önce canlı, sonra önbellekten (canlı sonuç önbelleğe yazılır)
    body = {"grid_km": 5, "time_limit": 4, "overrides": {"demand.safety_margin": 0.05}}
    r3 = c.post("/api/solve", json=body)
    assert r3.status_code == 202
    s3 = wait_final(ctx["get"], r3.json()["job_id"], 60)
    assert s3["state"] == "done" and s3["result"]["ok"]
    r4 = c.post("/api/solve", json=body)
    assert r4.status_code == 200 and r4.json()["from_cache"]
    assert r4.json()["result"]["bounds"]["ub_tl"] == s3["result"]["bounds"]["ub_tl"]


def test_cancel_mid_run(ctx):
    c, get = ctx["c"], ctx["get"]
    body = {**LONG, "overrides": {"demand.safety_margin": 0.07}}     # önceden çözülmüş kayıt bu probleme uymaz
    jid = c.post("/api/solve", json=body).json()["job_id"]
    _running(get, jid, min_progress=4)
    t = time.monotonic()
    r = c.post(f"/api/jobs/{jid}/cancel")
    assert r.status_code == 200 and r.json()["cancel_requested"]
    s = wait_final(get, jid, 30)
    assert time.monotonic() - t < 5.0
    assert s["state"] == "cancelled" and s["cancel_reason_tr"] == "Kullanıcı iptal etti."
    assert s["result"]["status"] == "durduruldu" and s["result"]["check"]["ok"]   # en iyi çözüm yine gelir
    assert s["run_s"] < 30
    # iptal edilen sonuç önbelleğe yazılmaz
    r2 = c.post("/api/solve", json={**body, "use_cache": True})
    assert r2.status_code == 202
    c.post(f"/api/jobs/{r2.json()['job_id']}/cancel")


def test_replace_latest_wins(ctx):
    c, get = ctx["c"], ctx["get"]
    for j in c.get("/api/jobs").json()["jobs"]:
        if j["state"] not in ("done", "failed", "cancelled"):
            wait_final(get, j["job_id"], 30)
    a = c.post("/api/solve", json={**LONG, "client_id": "arayuz"}).json()["job_id"]
    _running(get, a)
    b_body = {"grid_km": 5, "time_limit": 4, "use_cache": False, "client_id": "arayuz", "replace": True,
              "overrides": {"demand.safety_margin": 0.02}}
    rb = c.post("/api/solve", json=b_body)
    assert rb.status_code == 202 and rb.json()["replaced"] == [a]
    b = rb.json()["job_id"]
    # B koşarken gelen C kuyrukta; D (replace) C'yi kuyruktayken, B'yi çalışırken iptal eder
    _running(get, b, min_progress=1)
    cj = c.post("/api/solve", json={**b_body, "replace": False, "overrides": {"demand.safety_margin": 0.03}}).json()
    assert cj["state"] == "queued" and cj["position"] == 1
    rd = c.post("/api/solve", json={**b_body, "overrides": {"demand.safety_margin": 0.04}})
    assert set(rd.json()["replaced"]) == {b, cj["job_id"]}
    sa = wait_final(get, a, 30)
    assert sa["state"] == "cancelled" and sa["replaced_by"] == b and sa["result"]["status"] == "durduruldu"
    sc = get(f"/api/jobs/{cj['job_id']}")
    assert sc["state"] == "cancelled" and sc["result"] is None and sc["replaced_by"] == rd.json()["job_id"]
    assert wait_final(get, b, 30)["state"] == "cancelled"
    sd = wait_final(get, rd.json()["job_id"], 60)
    assert sd["state"] == "done" and sd["result"]["ok"]


def _hospitals(pre: dict) -> list[dict]:
    return [{"cell_id": h["cell_id"]} for h in pre["hospitals"]]


def test_evaluate_manual_open_set(ctx):
    c, get = ctx["c"], ctx["get"]
    lb, ub = PRE5["bounds"]["lb_tl"], PRE5["bounds"]["ub_tl"]
    assert lb == ub                                                     # 5 km varsayılanı kanıtlı optimum
    r = c.post("/api/evaluate", json={"grid_km": 5, "hospitals": _hospitals(PRE5)})
    assert r.status_code == 202
    s = wait_final(get, r.json()["job_id"], 60)
    res = s["result"]
    assert s["state"] == "done" and res["ok"] and res["check"]["ok"] and res["method"] == "motor_evaluate"
    assert abs(res["objective_tl"] - ub) <= 1e-7 * ub                   # optimumun açık kümesi → aynı maliyet
    assert res["costs"]["toplam"] == pytest.approx(res["objective_tl"], rel=1e-9)
    assert len(res["duals"]["marginal_cost_tl"]) == PRE5["instance"]["I"] and res["duals"]["bed_value_tl"]
    cmp_ = res["comparison"]
    assert cmp_["available"] and abs(cmp_["pct_vs_best"]) < 1e-7 and "optimumla aynı" in cmp_["message_tr"]
    # bir hastane eksik (olurlu kalan ilk) → optimumdan pahalı ya da eşit
    for drop in range(len(PRE5["hospitals"]) - 1, -1, -1):
        hs = [h for i, h in enumerate(_hospitals(PRE5)) if i != drop]
        s2 = wait_final(get, c.post("/api/evaluate", json={"grid_km": 5, "hospitals": hs}).json()["job_id"], 60)
        if s2["result"]["ok"]:
            break
    res2 = s2["result"]
    assert res2["check"]["ok"] and res2["kpis"]["n_hospitals"] == len(PRE5["hospitals"]) - 1
    assert res2["objective_tl"] >= lb * (1 - 1e-9)
    assert res2["comparison"]["pct_vs_best"] >= -1e-9 and "daha pahalı" in res2["comparison"]["message_tr"]
    # karışık: bir hastane V-C (yatak serbest), biri 150 yatakta sabit, kalanı serbest
    hs3 = _hospitals(PRE5)
    hs3[0] = {"cell_id": hs3[0]["cell_id"], "class": "V-C"}
    hs3[1] = {"cell_id": hs3[1]["cell_id"], "beds": 150}
    s3 = wait_final(get, c.post("/api/evaluate", json={"grid_km": 5, "hospitals": hs3}).json()["job_id"], 60)
    res3 = s3["result"]
    assert res3["ok"] and res3["check"]["ok"] and res3["method"] == "karisik"
    by = {h["cell_id"]: h for h in res3["hospitals"]}
    assert by[hs3[0]["cell_id"]]["class"] == "V-C" and by[hs3[0]["cell_id"]]["class_given"]
    assert by[hs3[1]["cell_id"]]["beds"] == pytest.approx(150) and by[hs3[1]["cell_id"]]["class"] == "IV-C"
    assert res3["objective_tl"] >= lb * (1 - 1e-9) and res3["comparison"]["pct_vs_best"] > 0
    # aynı değerlendirme önbellekten (karşılaştırma yeniden hesaplanır)
    r4 = c.post("/api/evaluate", json={"grid_km": 5, "hospitals": hs3})
    assert r4.status_code == 200 and r4.json()["from_cache"] and r4.json()["result"]["comparison"]["available"]


def test_evaluate_without_reference_and_noncandidate_cell(ctx):
    c, get = ctx["c"], ctx["get"]
    hs = _hospitals(PRE5)
    # aday olmayan bir hücre (su/boş) eklenir: 'aç' kilidiyle aday kümesine girer
    from backend.model.instance import build_instance
    from backend.model.params import Params
    inst = build_instance(5, Params.load({"grid.cell_km": 5}), None)
    cand = set(map(str, inst.s.cand_ids))
    grid = orjson.loads((ROOT / "data/processed/grid/grid_5km_cells.geojson").read_bytes())
    extra = next(f["properties"]["id"] for f in grid["features"] if f["properties"]["id"] not in cand)
    body = {"grid_km": 5, "hospitals": hs + [{"cell_id": extra}], "overrides": {"demand.safety_margin": 0.01}}
    s = wait_final(get, c.post("/api/evaluate", json=body).json()["job_id"], 60)
    res = s["result"]
    assert res["ok"] and res["check"]["ok"] and res["added_candidates"] == [extra]
    assert res["comparison"]["available"] is False                     # bu parametrelerle çözülmüş optimum yok


def test_bounds_job(ctx):
    c, get = ctx["c"], ctx["get"]
    r = c.post("/api/bounds", json={"grid_km": 5, "time_limit": 12})
    assert r.status_code == 202
    s = wait_final(get, r.json()["job_id"], 90)
    res = s["result"]
    assert s["state"] == "done" and res["ok"] and res["status"] == "tamam"
    pre = ctx["get"]("/api/bounds/5")
    assert res["max_hospitals_grid"] == pre["max_hospitals_grid"] == 454
    assert res["capacity"]["applies"] == pre["capacity"]["applies"]
    for k in ("coverage_lscp", "coverage_lscp_k_list"):
        if res[k]["proven"]:
            assert res[k]["value"] == pre[k]["value"], k               # 25 / 26 (kanıtlı)
    pm = res["p_min"]
    assert pm["lower_bound"] <= pre["p_min"]["value"] <= pm["value"]    # kanıtlı p_min = 27 arada
    assert res["time_s"] <= 12 + 3


def test_scenario_presets_solve_5km(ctx):
    """Altı hazır senaryo 5 km'de canlı çözülür (bütçe senaryosu varsayılan süre sınırıyla, 15 sn)."""
    c, get = ctx["c"], ctx["get"]
    sc = {s["id"]: s for s in get("/api/meta")["scenarios"]}
    jobs = {}
    for sid, s in sc.items():
        tl = 15 if s["mode"] == "butce" else 5
        r = c.post("/api/solve", json={"grid_km": 5, "time_limit": tl, "overrides": s["overrides"], "use_cache": False})
        assert r.status_code == 202, (sid, r.text)
        jobs[sid] = r.json()["job_id"]
    res = {sid: wait_final(get, jid, 120)["result"] for sid, jid in jobs.items()}
    for sid, r in res.items():
        assert r["ok"] and r["check"]["ok"], (sid, r.get("message_tr"))
    pre_acc = PRE5["costs"]["erisim"]
    b = res["dusuk_butce"]
    assert b["mode"] == "butce" and b["costs"]["acilis"] <= 346e9 * (1 + 1e-9) and b["costs"]["erisim"] > pre_acc
    assert res["sadece_genel"]["kpis"]["count_by_class"]["V-D"] == 0
    assert res["nufus_2030"]["kpis"]["total_demand_beds"] > PRE5["kpis"]["total_demand_beds"]
    assert res["erisim_oncelikli"]["instance"]["lambda_tl_per_person_km"] > \
        res["varsayilan"]["instance"]["lambda_tl_per_person_km"] * 1.7


def test_cancel_bounds_and_evaluate_without_restart(ctx):
    """Bulgu 2: sınır kartı ve manuel değerlendirme işleri iptali izler (HiGHS kesme geri çağrısı); eskiden iptal 15 sn
    sonra işçiyi yeniden başlatıyor, tüm sıcak önbellekleri siliyordu."""
    c, get = ctx["c"], ctx["get"]
    restarts0 = get("/api/health")["restarts"]
    jid = c.post("/api/bounds", json={"grid_km": 3, "time_limit": 120, "use_cache": False,
                                      "overrides": {"demand.safety_margin": 0.01}}).json()["job_id"]
    _running(get, jid, min_progress=1)
    time.sleep(2.0)
    t = time.monotonic()
    c.post(f"/api/jobs/{jid}/cancel")
    s = wait_final(get, jid, 30)
    assert s["state"] == "cancelled" and time.monotonic() - t < 8.0, (s["state"], time.monotonic() - t)
    pre2 = orjson.loads((ROOT / "precomputed" / "default_2km.json").read_bytes())
    jid = c.post("/api/evaluate", json={"grid_km": 2, "hospitals": _hospitals(pre2), "use_cache": False}).json()[
        "job_id"]
    _running(get, jid, min_progress=2)
    time.sleep(1.0)
    t = time.monotonic()
    c.post(f"/api/jobs/{jid}/cancel")
    s = wait_final(get, jid, 30)
    assert s["state"] == "cancelled" and time.monotonic() - t < 6.0, (s["state"], time.monotonic() - t)
    assert get("/api/health")["restarts"] == restarts0


def test_worker_crash_recovery(ctx):
    c, get = ctx["c"], ctx["get"]
    old = get("/api/health")["worker"]["pid"]
    jid = c.post("/api/solve", json=LONG).json()["job_id"]
    _running(get, jid, min_progress=3)
    kids = descendants(old)
    kill_hard(old)
    s = wait_final(get, jid, 30)
    assert s["state"] == "failed" and "yeniden başlatıldı" in s["error"]["message_tr"]
    h = wait_ready(get)
    assert h["restarts"] == 1 and h["worker"]["pid"] != old and h["worker"]["incarnation"] == 2
    assert "kapandı" in h["restart_log"][0]["reason_tr"]
    for p in kids + [old]:
        assert wait_for(lambda p=p: not pid_alive(p), 5.0), f"ölen işçinin alt süreci kaldı: {p}"
    s2 = wait_final(get, c.post("/api/solve", json={"grid_km": 5, "time_limit": 4, "use_cache": False,
                                                    "overrides": {"demand.safety_margin": 0.06}}).json()["job_id"], 60)
    assert s2["state"] == "done" and s2["result"]["ok"] and s2["result"]["check"]["ok"]
