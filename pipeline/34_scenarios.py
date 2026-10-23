"""Faz 4 — hazır senaryoların (şartname 6.3) önceden çözülmesi ve önceden çözülmüş dosyalara harita katmanlarının eklenmesi.

Alt komutlar (repo kökünden):
  .venv/bin/python pipeline/34_scenarios.py solve  [--grids 5,3,2] [--only id1,id2] [--time KM=sn] [--gap KM=g]
        → precomputed/scenario_<id>_<km>km.json (data/reference/senaryolar.yaml'daki her senaryo; 'varsayilan'
          senaryosunun dosyası precomputed/default_<km>km.json'dur, burada çözülmez)
  .venv/bin/python pipeline/34_scenarios.py enrich [--files ad1,ad2]
        → önceden çözülmüş sonuçlara (default_*, scenario_*) harita katmanları ('layers'), hastane boyut ayrıntısı
          ('hospitals[].size'), akış nüfusu ('assignment.population') ve kişi başı maliyet ('kpis.cost_per_capita_tl')
          eklenir; çözüm DEĞİŞMEZ (aynı açık küme, sınıf, yatak ve atama payları; backend/model/layers.py)
  .venv/bin/python pipeline/34_scenarios.py index
        → precomputed/scenarios.json (senaryo × ızgara: dosya adı, durum, amaç, alt sınır, gap, hastane sayısı, süre)
  .venv/bin/python pipeline/34_scenarios.py live   [--grids 5,3,2] [--out yol.json]
        → canlı denetim: sunucu uygulaması (backend.app, gerçek çözücü işçisi) her ızgarada yeniden başlatılır ve altı
          senaryo sırayla önbelleksiz (use_cache=false), ızgaranın önerilen süre sınırıyla çözülür; sonuçlar
          precomputed/scenarios.json 'live_check' alanına yazılır (ölçüm ortamı dosyada)

Süre ve hedef gap (SCENARIO_RUNS): 5 ve 3 km 300 sn, 2 km 600 sn. 300 sn ve üstünde solve() Lagrange → iyileştirme evresi →
tam MILP yolunu izler (backend/model/solve.py FULL_MIP_MIN_TIME). Başlangıç adayları: aynı parametre ve yapıyla yazılmış
önceki dosyanın çözümü, varsayılan çözümün açık kümesi (bu parametrelerle yeniden değerlendirilir) ve bütçe modunda
en düşük açılış maliyetli çözüm (erişim ağırlığı sıfırken toplam modu, MINOPEN_RUNS; bütçeye sığarsa olurlu bir
başlangıçtır: canlı bütçe sezgiseli 2 km'de 346 milyar TL'de 60 sn içinde olurlu çözüm bulamıyordu, Faz 4 ölçümü).
En ucuz aday MIP başlangıcıdır; yeniden çalıştırma çözümü kötüleştirmez.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

PRECOMP = ROOT / "precomputed"
INDEX = PRECOMP / "scenarios.json"
SCENARIOS_YAML = ROOT / "data" / "reference" / "senaryolar.yaml"
SCENARIO_RUNS = {5: (300.0, 1e-4), 3: (300.0, 1e-4), 2: (600.0, 1e-3)}
MINOPEN_RUNS = {5: 60.0, 3: 90.0, 2: 180.0}          # en düşük açılış maliyeti çözümü (bütçe başlangıcı)
LIVE_TIME = {5: 15.0, 3: 15.0, 2: 60.0}              # backend/app/config.py RECOMMENDED_TIME_LIMIT_S ile aynı


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _kv(arg: str | None, cast) -> dict:
    out = {}
    for part in (arg or "").split(","):
        if part.strip():
            k, v = part.split("=", 1)
            out[int(k)] = cast(v.strip())
    return out


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)
    print(f"Yazıldı: {path.relative_to(ROOT)} ({path.stat().st_size / 1e6:.2f} MB)", flush=True)


def scenarios() -> list[dict]:
    import yaml
    return yaml.safe_load(SCENARIOS_YAML.read_text(encoding="utf-8")) or []


def scenario_file(sid: str, km: int) -> Path:
    return PRECOMP / (f"default_{km}km.json" if sid == "varsayilan" else f"scenario_{sid}_{km}km.json")


def solution_from_result(inst, r):
    """Sonuç sözlüğünden Solution (hücre kimlikleriyle; yay indisleri yapıdan bulunur). Yapı tutmazsa ValueError."""
    from backend.model.instance import Solution
    s = inst.s
    pos = {str(c): j for j, c in enumerate(s.cand_ids)}
    code = {c.code: k for k, c in enumerate(inst.classes)}
    try:
        oj = np.array([pos[h["cell_id"]] for h in r["hospitals"]], dtype=np.int64)
    except KeyError as ex:
        raise ValueError(f"hastane hücresi bu yapıda aday değil: {ex}") from None
    cls = np.array([code[h["class"]] for h in r["hospitals"]], dtype=np.int64)
    beds = np.array([h["beds"] for h in r["hospitals"]], dtype=float)
    did = {str(c): i for i, c in enumerate(s.demand_ids)}
    x = np.zeros(inst.nA)
    a = r["assignment"]
    for dc, hidx, share in zip(a["demand_cell"], a["hospital"], a["share"]):
        i = did[dc]
        js = s.arc_j[s.arc_ptr[i]:s.arc_ptr[i + 1]]
        hit = np.flatnonzero(js == oj[hidx])
        if not len(hit):
            raise ValueError(f"atama yayı bu yapıda yok: {dc} → {r['hospitals'][hidx]['cell_id']}")
        x[s.arc_ptr[i] + int(hit[0])] = share
    order = np.argsort(oj)
    return Solution(oj[order], cls[order], beds[order], x, source="dosya")


def _instance_for(r: dict):
    from backend.model.instance import build_instance
    from backend.model.params import Params
    params = Params.load(r.get("overrides") or {})
    return build_instance(int(r["grid_km"]), params, r.get("locks") or None), params


# ---------------------------------------------------------------------- enrich
def enrich_result(r: dict) -> bool:
    """Katmanları ve kart ayrıntılarını (yeniden) hesaplar; çözüm aynı kalır. Döner: değişti mi."""
    from backend.model import layers
    from backend.model.solve import _jsonable
    if not r.get("ok"):
        return False
    inst, params = _instance_for(r)
    if params.hash() != r.get("params_hash"):
        raise ValueError("parametre özeti bugünkü kayıtla uyuşmuyor")
    if inst.s.key != r.get("structure_hash"):
        raise ValueError("yapı özeti uyuşmuyor")
    sol = solution_from_result(inst, r)
    obj = sol.objective_tl(inst)
    if abs(obj - r["bounds"]["ub_tl"]) > 1e-6 * abs(r["bounds"]["ub_tl"]):
        raise ValueError(f"çözümün amacı ({obj}) dosyadakiyle ({r['bounds']['ub_tl']}) aynı değil")
    # dosyadaki hastane sırası = sol.open_j sırası (solve() per_hospital sırası)
    order = [str(c) for c in inst.s.cand_ids[sol.open_j]]
    if order != [h["cell_id"] for h in r["hospitals"]]:
        raise ValueError("hastane sırası beklenenden farklı")
    for h, sz in zip(r["hospitals"], layers.size_info(inst, sol)):
        h["size"] = _jsonable(sz)
    s = inst.s
    did = {str(c): i for i, c in enumerate(s.demand_ids)}
    a = r["assignment"]
    pop = [float(s.w[did[dc]] * share) for dc, share in zip(a["demand_cell"], a["share"])]
    a["population"] = pop
    k = r["kpis"]
    k["cost_per_capita_tl"] = r["costs"]["toplam"] / k["population"] if k["population"] > 0 else None
    r["layers"] = _jsonable(layers.map_layers(inst, sol))
    r.setdefault("precomputed", {})["enriched_at"] = _now()
    return True


def run_enrich(names: list[str] | None) -> None:
    files = ([PRECOMP / f"{n.removesuffix('.json')}.json" for n in names] if names else
             sorted(PRECOMP.glob("default_*km.json")) + sorted(PRECOMP.glob("scenario_*_*km.json")))
    for f in files:
        r = json.loads(f.read_text(encoding="utf-8"))
        t = time.perf_counter()
        try:
            changed = enrich_result(r)
        except ValueError as ex:
            print(f"{f.name}: ATLANDI ({ex})", flush=True)
            continue
        if changed:
            print(f"{f.name}: katmanlar eklendi ({time.perf_counter() - t:.1f} sn)", flush=True)
            _write(f, r)


# ---------------------------------------------------------------------- solve
def _min_opening_start(km: int, ov: dict, inst):
    """Bütçe modu için olurlu başlangıç: erişim ağırlığı sıfırken toplam modunun çözümü (en düşük açılış maliyeti),
    bütçe modunun örneğinde yeniden değerlendirilir. Bütçeye sığmazsa None."""
    from backend.model.evaluate import evaluate
    from backend.model.solve import solve
    ov0 = {k: v for k, v in ov.items() if k not in ("solver.objective_mode", "solver.budget_tl")}
    ov0["access.hospital_visits_per_capita"] = 0
    r0 = solve(km, ov0, time_limit=MINOPEN_RUNS[km], gap=1e-3)
    if not r0.get("ok"):
        print(f"  en düşük açılış çözümü yok: {r0.get('message_tr')}", flush=True)
        return None, None
    inst0, _ = _instance_for(r0)
    sol0 = solution_from_result(inst0, r0)
    info = {"opening_tl": r0["costs"]["acilis"], "lb_tl": r0["bounds"]["lb_tl"], "n": r0["kpis"]["n_hospitals"],
            "status": r0["status"], "time_limit_s": MINOPEN_RUNS[km]}
    print(f"  en düşük açılış: {info['opening_tl'] / 1e9:.3f} milyar TL (alt sınır {info['lb_tl'] / 1e9:.3f}), "
          f"{info['n']} hastane", flush=True)
    if inst0.s.key != inst.s.key:
        return None, info
    ev = evaluate(inst, sol0.open_j, gap=1e-3, time_limit=60.0)
    if not ev.ok:
        ev = evaluate(inst, sol0.open_j, cls=sol0.cls)
    return (ev.solution if ev.ok else None), info


def run_solve(grids: list[int], only: list[str] | None, times: dict, gaps: dict) -> None:
    from backend.model.evaluate import evaluate
    from backend.model.instance import build_instance
    from backend.model.params import Params
    from backend.model.solve import solve
    for km in grids:
        for sc in scenarios():
            sid = sc["id"]
            if sid == "varsayilan" or (only and sid not in only):
                continue
            tl, gap = SCENARIO_RUNS[km]
            tl, gap = times.get(km, tl), gaps.get(km, gap)
            ov = dict(sc.get("overrides") or {})
            ov["grid.cell_km"] = km
            params = Params.load(ov)
            inst = build_instance(km, params)
            print(f"{km} km {sid}: {params.objective_mode}, süre {tl:g} sn, hedef gap {gap:g}", flush=True)
            cands = []
            prev = scenario_file(sid, km)
            if prev.exists():
                old = json.loads(prev.read_text(encoding="utf-8"))
                if old.get("ok") and old.get("params_hash") == params.hash() and old.get("structure_hash") == inst.s.key:
                    try:
                        cands.append(("onceki_dosya", solution_from_result(inst, old)))
                    except ValueError:
                        pass
            dflt = scenario_file("varsayilan", km)
            if dflt.exists():
                d = json.loads(dflt.read_text(encoding="utf-8"))
                try:
                    inst_d, _ = _instance_for(d)
                    if inst_d.s.key == inst.s.key:
                        sol_d = solution_from_result(inst_d, d)
                        ev = evaluate(inst, sol_d.open_j, gap=1e-3, time_limit=60.0)
                        if ev.ok:
                            cands.append(("varsayilan_acik_kume", ev.solution))
                except ValueError:
                    pass
            minopen = None
            if params.objective_mode == "butce":
                sol_m, minopen = _min_opening_start(km, ov, inst)
                if sol_m is not None:
                    cands.append(("en_dusuk_acilis", sol_m))
            start_info = [{"source": src, "objective_tl": sol.objective_tl(inst), "n_hospitals": sol.n_open,
                           "opening_tl": sol.opening_tl(inst)} for src, sol in cands]
            start, start_src = None, None
            if cands:
                start_src, start = min(cands, key=lambda c: c[1].objective_tl(inst))
                for si in start_info:
                    si["chosen"] = si["source"] == start_src
            t0 = time.perf_counter()
            r = solve(km, ov, time_limit=tl, gap=gap, compute_duals=True, start=start)
            r["scenario"] = sid
            r["precomputed"] = {"generated_by": "pipeline/34_scenarios.py solve", "generated_at": _now(),
                                "scenario": sid, "time_limit_s": tl, "target_gap": gap,
                                "wall_s": time.perf_counter() - t0, "start_source": start_src,
                                "start_candidates": start_info, "min_opening": minopen}
            if r.get("ok"):
                b = r["bounds"]
                print(f"  {r['status']} UB {b['ub_tl'] / 1e9:.4f} LB {b['lb_tl'] / 1e9:.4f} gap "
                      f"{100 * b['gap']:.3f}% ({b['lb_source']}, {b['mip_kind']}), {r['kpis']['n_hospitals']} hastane, "
                      f"kaynak {r['solution_source']}, {r['times']['total_s']:.0f} sn, denetim {r['check']['ok']}",
                      flush=True)
                _write(prev, r)
            else:
                print(f"  ÇÖZÜM YOK: {r['status']} {r.get('message_tr')}", flush=True)


# ---------------------------------------------------------------------- index
def run_index() -> None:
    from backend.model.params import Params
    old = json.loads(INDEX.read_text(encoding="utf-8")) if INDEX.exists() else {}
    out = {"generated_by": "pipeline/34_scenarios.py index", "generated_at": _now(),
           "note_tr": "Hazır senaryoların önceden çözülmüş sonuçları (dosya adı /api/precomputed/<ad> ile okunur).",
           "scenarios": {}}
    if old.get("live_check"):
        out["live_check"] = old["live_check"]
    for sc in scenarios():
        sid = sc["id"]
        grids = {}
        for km in (5, 3, 2):
            f = scenario_file(sid, km)
            if not f.exists():
                continue
            r = json.loads(f.read_text(encoding="utf-8"))
            ov = dict(sc.get("overrides") or {})
            ov["grid.cell_km"] = km
            if not r.get("ok") or Params.load(ov).hash() != r.get("params_hash"):
                print(f"{f.name}: senaryonun bugünkü parametreleriyle uyuşmuyor, dizine alınmadı", flush=True)
                continue
            b = r["bounds"]
            grids[str(km)] = {"name": f.stem, "url": f"/api/precomputed/{f.stem}", "status": r["status"],
                              "ub_tl": b["ub_tl"], "lb_tl": b["lb_tl"], "gap": b["gap"],
                              "n_hospitals": r["kpis"]["n_hospitals"], "time_limit_s": r["time_limit_s"],
                              "target_gap": r["target_gap"], "params_hash": r["params_hash"],
                              "has_layers": "layers" in r}
        out["scenarios"][sid] = {"title": sc.get("title"), "grids": grids}
    _write(INDEX, out)


# ---------------------------------------------------------------------- live
def run_live(grids: list[int], out_path: Path | None) -> None:
    """Gerçek sunucu uygulaması + çözücü işçisi; her ızgarada yeni sunucu (soğuk işçi), senaryolar sırayla."""
    import os
    import platform
    import tempfile

    from fastapi.testclient import TestClient

    from backend.app.config import Settings
    from backend.app.main import create_app
    rows = []
    for km in grids:
        with tempfile.TemporaryDirectory() as td:
            app = create_app(Settings(cache_dir=Path(td), seed_precomputed=False))
            with TestClient(app, base_url="http://127.0.0.1:8000") as c:
                t_wait = time.perf_counter()
                while not c.get("/api/health").json().get("solver_ready"):
                    if time.perf_counter() - t_wait > 120:
                        raise RuntimeError("çözücü hazır olmadı")
                    time.sleep(0.2)
                for sc in scenarios():
                    body = {"grid_km": km, "overrides": dict(sc.get("overrides") or {}), "use_cache": False,
                            "time_limit": LIVE_TIME[km], "duals": True}
                    t0 = time.perf_counter()
                    jid = c.post("/api/solve", json=body).json()["job_id"]
                    t1pct = None
                    with c.stream("GET", f"/api/jobs/{jid}/events") as s:
                        ev_name = None
                        for line in s.iter_lines():
                            if line.startswith("event:"):
                                ev_name = line[6:].strip()
                            elif line.startswith("data:") and ev_name == "progress":
                                d = json.loads(line[5:])
                                if t1pct is None and d.get("gap") is not None and d["gap"] <= 0.01:
                                    t1pct = d.get("t")
                    snap = c.get(f"/api/jobs/{jid}").json()
                    res = snap.get("result") or {}
                    b = res.get("bounds") or {}
                    row = {"grid_km": km, "scenario": sc["id"], "state": snap["state"], "status": res.get("status"),
                           "ok": res.get("ok"), "gap": b.get("gap"), "ub_tl": b.get("ub_tl"),
                           "n_hospitals": (res.get("kpis") or {}).get("n_hospitals"),
                           "time_limit_s": LIVE_TIME[km], "t_to_1pct_s": t1pct,
                           "wall_s": round(time.perf_counter() - t0, 2), "solution_source": res.get("solution_source"),
                           "seeds": res.get("seeds")}
                    row["works_live"] = bool(res.get("ok")) and b.get("gap") is not None and b["gap"] <= 0.05
                    rows.append(row)
                    print(json.dumps({k: v for k, v in row.items() if k != "seeds"}, ensure_ascii=False), flush=True)
    rec = {"measured_at": _now(), "by": "pipeline/34_scenarios.py live",
           "environment": {"platform": platform.platform(), "python": platform.python_version(),
                           "cpus": os.cpu_count()},
           "rule_tr": "Önbelleksiz canlı çözüm (use_cache=false), ızgaranın önerilen süre sınırı; her ızgarada yeni "
                      "sunucu, senaryolar dosyadaki sırayla. works_live: olurlu çözüm ve fark ≤ %5.",
           "rows": rows}
    if out_path is not None:
        out_path.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    idx = json.loads(INDEX.read_text(encoding="utf-8")) if INDEX.exists() else {"scenarios": {}}
    old_rows = [r for r in (idx.get("live_check") or {}).get("rows", []) if r.get("grid_km") not in grids]
    if old_rows:                        # yalnız bazı ızgaralar yeniden ölçüldüyse diğerlerinin satırları korunur
        rec["rows"] = sorted(old_rows + rows, key=lambda r: -r["grid_km"])
        rec["partial_remeasure"] = {"grids": grids, "measured_at": rec["measured_at"]}
    idx["live_check"] = rec
    _write(INDEX, idx)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=("solve", "enrich", "index", "live"))
    ap.add_argument("--grids", default="5,3,2")
    ap.add_argument("--only", default=None)
    ap.add_argument("--files", default=None)
    ap.add_argument("--time", default=None)
    ap.add_argument("--gap", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    grids = [int(g) for g in args.grids.split(",")]
    if args.what == "solve":
        run_solve(grids, args.only.split(",") if args.only else None, _kv(args.time, float), _kv(args.gap, float))
    elif args.what == "enrich":
        run_enrich(args.files.split(",") if args.files else None)
    elif args.what == "index":
        run_index()
    else:
        run_live(grids, Path(args.out) if args.out else None)


if __name__ == "__main__":
    main()
