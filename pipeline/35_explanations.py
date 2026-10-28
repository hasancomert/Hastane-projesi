"""Faz 6 — açıklama katmanının önceden hesaplanması (şartname 7.3–7.9): "neden bu çözüm optimum?".

Alt komutlar (repo kökünden):
  .venv/bin/python pipeline/35_explanations.py explain  [--grids 3,5] [--scenarios varsayilan,...] [--exact KM,...]
                                                      [--n-time KM=sn]
        → precomputed/explain_<km>km.json (varsayılan) ve explain_<senaryo>_<km>km.json (hazır senaryolar):
          her hastane için "bu hastane kapatılırsa" (hızlı tahmin; --exact verilen ızgaralarda sınıf seçimli kesin
          değerlendirme), N − 1 / N + 1 (varsayılan parametrelerde precomputed/p_sweep_*.json'un uzun çözülmüş
          noktaları, senaryolarda sıcak başlangıçlı canlı sabit_p çözümü), "neden bu boyut?", gölge fiyatlar, model
          düzeyindeki nedenler. Temel çözüm: precomputed/default_<km>km.json ya da scenario_<id>_<km>km.json.
  .venv/bin/python pipeline/35_explanations.py reopt    [--grids 3] [--time KM=sn] [--limit N]
        → explain_<km>km.json'daki olurlu kapatmalara "j yasakken tam yeniden çözüm" ekler (reopt alanı)
  .venv/bin/python pipeline/35_explanations.py pareto   [--grids 3,5] [--time KM=sn]
        → precomputed/pareto_<km>km.json (λ × 0,25 … 4, 12 nokta; f = 1 noktası varsayılan çözüm)
  .venv/bin/python pipeline/35_explanations.py tornado  [--grids 3,5] [--time KM=sn]
        → precomputed/tornado_<km>km.json (16 satır × ±%20; temel = varsayılan çözüm)
  .venv/bin/python pipeline/35_explanations.py all      (sırayla: explain → pareto → tornado; reopt ayrı)

Kaldığı yerden sürer: her öğenin sonucu cache/precompute_35/<iş>.jsonl dosyasına yazılır (git dışı); yeniden
çalıştırmada aynı ayarlarla bitmiş öğeler atlanır (anahtar: parametre özeti + temel çözüm + ayarlar). Süre ve gap
ayarları dosyanın 'settings' alanında, her çözümün alt/üst sınırı ve gap'i kendi kaydında. İlerleme stdout'a yazılır.
Sonuç biçimleri iş türleriyle aynıdır (backend/app/task_explain.py, task_pareto.py, task_sensitivity.py; docs/API.md
§11); sunucu bu dosyaları aynı problem için anında döndürür.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PRECOMP = ROOT / "precomputed"
CKPT = ROOT / "cache" / "precompute_35"
SCENARIOS_YAML = ROOT / "data" / "reference" / "senaryolar.yaml"
# süre sınırları (sn): N ± 1 (senaryolarda canlı), Pareto noktası, tornado çalışması, kapatmanın yeniden çözümü.
# Gerekçe: 3 km canlı hedef 15 sn'dir; önceden hesaplananlarda sıcak başlangıçla 3 katı süre gap'i %0,1–0,3'e indirir
# (Faz 3 ölçümleri), 5 km'de 3–5 sn'de %0,3'ün altı.
N_TIME = {3: 45.0, 5: 20.0, 2: 90.0}
PARETO_TIME = {3: 45.0, 5: 20.0, 2: 90.0}
TORNADO_TIME = {3: 40.0, 5: 15.0, 2: 90.0}
REOPT_TIME = {3: 20.0, 5: 10.0, 2: 60.0}
GAP = 1e-3


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


def _jsonable(o):
    from backend.model.solve import _jsonable as j
    return j(o)


class Checkpoint:
    """Öğe sonuçları (JSON satırları): anahtar → sonuç. Aynı anahtar yeniden hesaplanmaz."""

    def __init__(self, name: str) -> None:
        CKPT.mkdir(parents=True, exist_ok=True)
        self.path = CKPT / f"{name}.jsonl"
        self.data: dict[str, dict] = {}
        if self.path.exists():
            for ln in self.path.read_text(encoding="utf-8").splitlines():
                try:
                    d = json.loads(ln)
                    self.data[d["key"]] = d["value"]
                except (ValueError, KeyError):
                    continue

    def get(self, key: str):
        return self.data.get(key)

    def put(self, key: str, value) -> None:
        value = _jsonable(value)
        self.data[key] = value
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"key": key, "value": value}, ensure_ascii=False) + "\n")


def _h(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


def scenarios() -> list[dict]:
    import yaml
    return yaml.safe_load(SCENARIOS_YAML.read_text(encoding="utf-8")) or []


def base_file(sid: str, km: int) -> Path:
    return PRECOMP / (f"default_{km}km.json" if sid == "varsayilan" else f"scenario_{sid}_{km}km.json")


def out_name(prefix: str, sid: str, km: int) -> str:
    return f"{prefix}_{km}km" if sid == "varsayilan" else f"{prefix}_{sid}_{km}km"


def load_base(sid: str, km: int):
    """Önceden çözülmüş temel sonuç → (sonuç, Params, Instance, Solution, hastaneler, sınırlar)."""
    from backend.model import explain as X
    from backend.model.instance import build_instance
    from backend.model.params import Params
    path = base_file(sid, km)
    r = json.loads(path.read_text(encoding="utf-8"))
    params = Params.load(r.get("overrides") or {})
    if params.hash() != r.get("params_hash"):
        raise SystemExit(f"{path.name}: parametre özeti bugünkü kayıtla uyuşmuyor; önce yeniden çözün.")
    locks = r.get("locks") or {"open": [], "closed": []}
    inst = build_instance(km, params, locks)
    hs = [{"cell_id": h["cell_id"], "class": h["class"], "beds": float(h["beds"])} for h in r["hospitals"]]
    ev = X.hospital_solution(inst, hs)
    if not ev.ok:
        raise SystemExit(f"{path.name}: temel çözüm değerlendirilemedi: {ev.message}")
    b = r["bounds"]
    bb = {"ub_tl": min(float(b["ub_tl"]), float(ev.objective_tl)), "lb_tl": b.get("lb_tl")}
    return r, params, inst, ev.solution, hs, bb, path


# ---------------------------------------------------------------------- explain
def run_explain(grids: list[int], sids: list[str], exact_grids: set[int], n_times: dict) -> None:
    from backend.app import task_explain as TE
    from backend.model import explain as X
    from backend.model.evaluate import assignment_duals
    for km in grids:
        for sid in sids:
            if not base_file(sid, km).exists():
                print(f"{km} km {sid}: temel dosya yok, atlandı", flush=True)
                continue
            r, params, inst, sol, hs, bb, path = load_base(sid, km)
            exact = km in exact_grids and sid == "varsayilan"     # senaryolarda hızlı tahmin (süre bütçesi)
            tl_n = float(n_times.get(km, N_TIME.get(km, 30.0)))
            settings = {"exact": exact, "n_time_limit_s": tl_n, "gap": GAP,
                        "close_neighbours": X.CLOSE_NEIGHBOURS, "close_exact_time_s": X.CLOSE_EXACT_TIME}
            ck = Checkpoint(f"explain_{sid}_{km}km")
            base_key = _h({"p": params.hash(), "hs": hs, "set": settings})
            ref = X.reference_of(inst, sol, bb)
            print(f"{km} km {sid}: {sol.n_open} hastane, {ref['ub_tl'] / 1e9:.4f} milyar TL, mod {inst.mode}, "
                  f"kesin={exact}", flush=True)
            T0 = time.perf_counter()
            items = []
            for t in range(sol.n_open):
                cid = str(inst.s.cand_ids[sol.open_j[t]])
                key = f"{base_key}:kapat:{cid}"
                got = ck.get(key)
                if got is None:
                    c = X.closure_effect(inst, sol, t, base_obj=float(sol.objective_tl(inst)), base_bounds=bb,
                                         exact=exact)
                    got = {k: v for k, v in c.items() if not k.startswith("_")}
                    ck.put(key, got)
                    print(f"  kapat {t + 1}/{sol.n_open} {cid}: "
                          + (f"Δ {got['delta_tl'] / 1e6:+.1f} milyon TL ({got['method']}, {got['time_s']:.1f} sn)"
                             if got.get("feasible") else f"olurlu değil ({got.get('reason')})"), flush=True)
                got = dict(got)
                if got.get("feasible") or got.get("reason") in ("R", "k_listesi"):
                    # cümle saklanan sayılardan yeniden yazılır (metin düzeltmeleri yeniden hesap gerektirmez)
                    got["message_tr"] = X.closure_message(got, n_remaining=sol.n_open - 1, mode=inst.mode,
                                                          R_km=float(inst.s.R_km), k_nearest=int(inst.s.k_nearest))
                items.append(got)
            # N ± 1
            sweep = TE._sweep_points(params, {"grid_km": km, "locks": inst.locks.as_dict()})
            for kind, p in (("n_eksi_1", sol.n_open - 1), ("n_arti_1", sol.n_open + 1)):
                if inst.mode == "butce":
                    continue
                key = f"{base_key}:{kind}"
                got = ck.get(key)
                if got is None:
                    pre = (sweep or {}).get(p)
                    if pre is not None:
                        pt = {k: v for k, v in pre.items() if k != "refine"}
                        pt["source"], pt["source_tr"] = "p_taramasi", "önceden hesaplanmış p taraması (uzun çözüm)"
                        X.apply_reference(pt, ref)
                    else:
                        t1 = time.perf_counter()
                        pt, _ = X.best_with_p(inst, sol, p, ref=ref, time_limit=tl_n, gap=GAP)
                        pt["wall_s"] = time.perf_counter() - t1
                    pt["message_tr"] = X.n_message(pt, ref)
                    if pt.get("ok"):
                        pt.setdefault("objective_tl", pt.get("ub_tl"))
                        pt["delta_tl"] = pt.get("fark_tl")
                    got = {**pt, "kind": kind}
                    ck.put(key, got)
                    print(f"  {kind} p = {p}: {got.get('source')} {got.get('status')} fark "
                          f"{(got.get('fark_tl') or 0) / 1e6:+.1f} milyon TL, gap içinde: {got.get('fark_gap_icinde')}",
                          flush=True)
                got = dict(got, message_tr=X.n_message(got, ref))
                items.append(got)
            duals = assignment_duals(inst, sol)
            items.append(X.size_explanations(inst, sol, duals))
            items.append(X.shadow_prices(inst, sol, duals))
            items.append(X.model_reasons(inst, sol))
            closes = [x for x in items if x.get("kind") == "hastane_kapat"]
            doc = {"generated_by": "pipeline/35_explanations.py explain", "generated_at": _now(), "grid_km": km,
                   "scenario": sid, "mode": inst.mode, "params_hash": params.hash(), "overrides": dict(params.overrides),
                   "locks": inst.locks.as_dict(), "settings": settings,
                   "base": {"source": path.name, "hospitals": hs, "bounds": bb, "status": r.get("status")},
                   "base_summary": {"objective_tl": float(sol.objective_tl(inst)), "n_hospitals": sol.n_open,
                                    "ub_tl": bb["ub_tl"], "lb_tl": bb.get("lb_tl"),
                                    "gap": ((bb["ub_tl"] - bb["lb_tl"]) / bb["ub_tl"]) if bb.get("lb_tl") else None,
                                    "opening_tl": ref["opening_tl"], "access_tl": ref["access_tl"],
                                    "pop_weighted_mean_km": ref["pop_weighted_mean_km"],
                                    "count_by_class": ref["count_by_class"], "mode": inst.mode, "lb_from": "dosya"},
                   "items": items, "closure_summary": TE.closure_summary(closes, inst.mode),
                   "p_sweep": (sweep or {}).get("_meta"), "ok": True, "status": "tamam",
                   "times": {"total_s": time.perf_counter() - T0}}
            old = PRECOMP / f"{out_name('explain', sid, km)}.json"
            if old.exists():                          # önceki reopt sonuçları korunur (aynı temel ve parametreyle)
                try:
                    prev = json.loads(old.read_text(encoding="utf-8"))
                    if prev.get("params_hash") == doc["params_hash"] and prev.get("base", {}).get("hospitals") == hs:
                        ro = {x["cell_id"]: x.get("reopt") for x in prev.get("items", [])
                              if x.get("kind") == "hastane_kapat" and x.get("reopt")}
                        for x in closes:
                            if x["cell_id"] in ro:
                                x["reopt"] = ro[x["cell_id"]]
                        doc["settings"]["reopt_time_limit_s"] = prev.get("settings", {}).get("reopt_time_limit_s")
                except (OSError, ValueError):
                    pass
            _write(old, _jsonable(doc))
            print(f"  özet: {doc['closure_summary']['summary_tr']}", flush=True)


def run_reopt(grids: list[int], times: dict, limit: int | None) -> None:
    from backend.model import explain as X
    for km in grids:
        path = PRECOMP / f"explain_{km}km.json"
        if not path.exists():
            raise SystemExit(f"{path.name} yok: önce 'explain'.")
        doc = json.loads(path.read_text(encoding="utf-8"))
        r, params, inst, sol, hs, bb, _ = load_base("varsayilan", km)
        if doc.get("params_hash") != params.hash() or doc["base"]["hospitals"] != hs:
            raise SystemExit(f"{path.name} temel çözümle uyuşmuyor; önce 'explain'.")
        tl = float(times.get(km, REOPT_TIME.get(km, 20.0)))
        ck = Checkpoint(f"reopt_{km}km")
        base_key = _h({"p": params.hash(), "hs": hs, "tl": tl, "gap": GAP})
        pos = {str(inst.s.cand_ids[j]): t for t, j in enumerate(sol.open_j)}
        closes = [x for x in doc["items"] if x.get("kind") == "hastane_kapat" and x.get("feasible")]
        closes.sort(key=lambda x: x["delta_tl"])
        if limit:
            closes = closes[:limit]
        for n, x in enumerate(closes):
            key = f"{base_key}:{x['cell_id']}"
            got = ck.get(key)
            if got is None:
                t = pos[x["cell_id"]]
                c = X.closure_effect(inst, sol, t, base_obj=float(sol.objective_tl(inst)), base_bounds=bb,
                                     exact=doc["settings"].get("exact", False))
                # başlangıç = kapatmanın (kesin) çözümü: yeniden çözüm ondan kötü olamaz
                got = X.reoptimize_closed(inst, sol, t, c, time_limit=tl, gap=GAP, base_bounds=bb)
                ck.put(key, got)
            x["reopt"] = got
            print(f"  reopt {n + 1}/{len(closes)} {x['cell_id']}: hızlı Δ {x['delta_tl'] / 1e6:+.1f}, yeniden çözüm Δ "
                  f"{(got.get('delta_tl') or 0) / 1e6:+.1f} milyon TL ({got.get('status')}, gap içinde: "
                  f"{got.get('fark_gap_icinde')})", flush=True)
        doc["settings"]["reopt_time_limit_s"] = tl
        doc["generated_at"] = _now()
        _write(path, _jsonable(doc))


# ---------------------------------------------------------------------- Pareto ve tornado
def run_pareto(grids: list[int], times: dict) -> None:
    from backend.model import explain as X
    for km in grids:
        r, params, inst, sol, hs, bb, path = load_base("varsayilan", km)
        tl = float(times.get(km, PARETO_TIME.get(km, 30.0)))
        ck = Checkpoint(f"pareto_{km}km")
        base_key = _h({"p": params.hash(), "hs": hs, "tl": tl, "gap": GAP})
        cache_pts = {}

        def on_point(pt, done, total):
            ck.put(f"{base_key}:{pt['factor']}", pt)
            print(f"  λ × {pt['factor']}: {pt.get('status')} {pt.get('n_hospitals')} hastane, açılış "
                  f"{(pt.get('opening_tl') or 0) / 1e9:.2f}, ort. {pt.get('pop_weighted_mean_km') or 0:.3f} km, asıl toplam "
                  f"{(pt.get('total_tl') or 0) / 1e9:.3f} milyar TL, gap {100 * (pt.get('gap') or 0):.3f}% "
                  f"({pt['time_s']:.0f} sn) [{done}/{total}]", flush=True)

        fs = [f for f in X.PARETO_FACTORS]
        todo = []
        for f in fs:
            got = ck.get(f"{base_key}:{float(f)}")
            if got is not None:
                cache_pts[float(f)] = got
            else:
                todo.append(f)
        T0 = time.perf_counter()
        print(f"{km} km Pareto: {len(fs)} nokta, {len(todo)} çözülecek, nokta başına {tl:.0f} sn", flush=True)
        res = X.pareto(km, dict(params.overrides), inst.locks.as_dict(), todo or [1.0], time_limit=tl, gap=GAP,
                       base={"hospitals": hs, "bounds": bb}, on_point=on_point)
        pts = {float(q["factor"]): q for q in res["points"]}
        pts.update(cache_pts)
        # özet yeniden (tüm noktalarla)
        full = dict(res)
        full["points"] = [pts[float(f)] for f in sorted(set(float(f) for f in fs) | {1.0}) if float(f) in pts]
        ok = [q for q in full["points"] if q.get("ok")]
        for q in ok:
            q["dominated"] = any((o["opening_tl"] <= q["opening_tl"] and o["pop_weighted_mean_km"] <= q["pop_weighted_mean_km"]
                                  and (o["opening_tl"] < q["opening_tl"] * (1 - 1e-9) or
                                       o["pop_weighted_mean_km"] < q["pop_weighted_mean_km"] * (1 - 1e-9)))
                                 for o in ok if o is not q)
        cur = next((q for q in ok if q["factor"] == 1.0), None)
        best = min(ok, key=lambda q: q["total_tl"])
        full["best_total_factor"] = best["factor"]
        if cur is not None:
            full["current_total_gap_tl"] = cur["total_tl"] - best["total_tl"]
            tol = (cur["ub_tl"] - cur["lb_tl"]) if cur.get("lb_tl") is not None else 0.0
            full["current_is_min_total"] = bool(full["current_total_gap_tl"] <= tol + 1e-9 * cur["total_tl"])
        full["summary_tr"] = X.pareto_summary(full)
        doc = {"generated_by": "pipeline/35_explanations.py pareto", "generated_at": _now(), "grid_km": km,
               "scenario": "varsayilan", "mode": inst.mode, "params_hash": params.hash(),
               "overrides": dict(params.overrides), "locks": inst.locks.as_dict(),
               "settings": {"time_limit_s": tl, "gap": GAP, "factors": list(X.PARETO_FACTORS)},
               "base": {"source": path.name, "bounds": bb, "n_hospitals": sol.n_open},
               **{k: v for k, v in full.items() if k not in ("status",)}, "status": "tamam", "ok": True,
               "times": {"total_s": time.perf_counter() - T0}}
        _write(PRECOMP / f"pareto_{km}km.json", _jsonable(doc))
        print(f"  özet: {doc['summary_tr']}", flush=True)


def run_tornado(grids: list[int], times: dict) -> None:
    from backend.model import explain as X
    for km in grids:
        r, params, inst, sol, hs, bb, path = load_base("varsayilan", km)
        tl = float(times.get(km, TORNADO_TIME.get(km, 30.0)))
        ck = Checkpoint(f"tornado_{km}km")
        base_key = _h({"p": params.hash(), "hs": hs, "tl": tl, "gap": GAP, "d": X.TORNADO_DELTA})
        base = {"objective_tl": bb["ub_tl"], "lb_tl": bb.get("lb_tl"), "n_hospitals": sol.n_open, "source": path.name,
                "opening_tl": float(sol.opening_tl(inst)), "access_tl": float(sol.access_tl(inst)),
                "hospitals": [str(c) for c in inst.s.cand_ids[sol.open_j]],
                "classes": [inst.classes[int(k)].code for k in sol.cls]}
        ids = [s for s, _ in X.TORNADO_SPECS]
        T0 = time.perf_counter()
        all_runs: dict = {}
        todo = []
        for sid in ids:
            lo, hi = ck.get(f"{base_key}:{sid}:dusuk"), ck.get(f"{base_key}:{sid}:yuksek")
            if lo is not None and hi is not None:
                all_runs[sid] = (lo, hi)
            else:
                todo.append(sid)
        print(f"{km} km tornado: {len(ids)} satır, {len(todo)} çözülecek, çalışma başına {tl:.0f} sn", flush=True)

        def on_run(rr, done, total):
            ck.put(f"{base_key}:{rr['id']}:{rr['side']}", rr)
            print(f"  {rr['id']} {rr['side']}: {rr.get('status')} Δ "
                  + (f"{100 * rr['delta_pct']:+.3f}% [{100 * (rr['delta_lo_pct'] or 0):+.3f}, "
                     f"{100 * (rr['delta_hi_pct'] or 0):+.3f}], Δn {rr['delta_n']:+d}" if rr.get("ok") else
                     str(rr.get("message_tr"))) + f" ({rr['time_s']:.0f} sn) [{done}/{total}]", flush=True)

        if todo:
            X.tornado(km, dict(params.overrides), inst.locks.as_dict(), keys=todo, time_limit=tl, gap=GAP, base=base,
                      on_run=on_run)
        # tüm satırlar denetim noktasından (sıra kayıttaki gibi)
        runs = []
        for sid in ids:
            for side in ("dusuk", "yuksek"):
                got = ck.get(f"{base_key}:{sid}:{side}")
                if got is not None:
                    runs.append(got)
        res = _tornado_from_runs(X, params, runs, base)
        doc = {"generated_by": "pipeline/35_explanations.py tornado", "generated_at": _now(), "grid_km": km,
               "scenario": "varsayilan", "mode": inst.mode, "params_hash": params.hash(),
               "overrides": dict(params.overrides), "locks": inst.locks.as_dict(),
               "settings": {"time_limit_s": tl, "gap": GAP, "delta": X.TORNADO_DELTA, "warm_start": "temel çözüm"},
               **res, "ok": True, "times": {"total_s": time.perf_counter() - T0}}
        _write(PRECOMP / f"tornado_{km}km.json", _jsonable(doc))
        print(f"  özet: {doc['summary_tr']}", flush=True)


def _tornado_from_runs(X, params, runs: list[dict], base: dict) -> dict:
    specs = X.tornado_specs(params)
    rows = []
    for spec in specs:
        lo = next((r for r in runs if r["id"] == spec["id"] and r["side"] == "dusuk"), None)
        hi = next((r for r in runs if r["id"] == spec["id"] and r["side"] == "yuksek"), None)
        if lo is None and hi is None:
            continue
        sw = [abs(r["delta_pct"]) for r in (lo, hi) if r and r.get("ok")]
        rows.append({"id": spec["id"], "keys": spec["keys"], "label_tr": spec["label_tr"], "unit": spec["unit"],
                     "status": spec["status"], "base": spec["base"], "low": lo, "high": hi,
                     "swing_pct": max(sw) if sw else None,
                     "range_pct": ((hi["delta_pct"] if hi and hi.get("ok") else 0.0) -
                                   (lo["delta_pct"] if lo and lo.get("ok") else 0.0))})
    rows.sort(key=lambda r: -(r["swing_pct"] or 0.0))
    out = {"status": "tamam" if len(rows) == len(specs) else "eksik", "delta": X.TORNADO_DELTA,
           "base": {k: v for k, v in base.items() if k not in ("hospitals", "classes")}, "rows": rows}
    out["summary_tr"] = X.tornado_summary(out)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["explain", "reopt", "pareto", "tornado", "all"])
    ap.add_argument("--grids", default="3,5")
    ap.add_argument("--scenarios", default=None, help="explain: senaryo kimlikleri (varsayılan: hepsi)")
    ap.add_argument("--exact", default="3,5", help="explain: kesin kapatma değerlendirmesi yapılacak ızgaralar")
    ap.add_argument("--n-time", default=None, help="explain: canlı N ± 1 süre sınırı, KM=sn")
    ap.add_argument("--time", default=None, help="pareto/tornado/reopt: süre sınırı, KM=sn")
    ap.add_argument("--limit", type=int, default=None, help="reopt: en çok bu kadar kapatma (en küçük Δ'dan)")
    a = ap.parse_args()
    grids = [int(x) for x in a.grids.split(",") if x.strip()]
    times = _kv(a.time, float)
    t0 = time.perf_counter()
    if a.cmd in ("explain", "all"):
        sids = [s["id"] for s in scenarios()] if a.scenarios is None else [x for x in a.scenarios.split(",") if x]
        if "varsayilan" in sids:
            sids = ["varsayilan"] + [s for s in sids if s != "varsayilan"]
        run_explain(grids, sids, {int(x) for x in a.exact.split(",") if x.strip()}, _kv(a.n_time, float))
    if a.cmd in ("pareto", "all"):
        run_pareto(grids, times)
    if a.cmd in ("tornado", "all"):
        run_tornado(grids, times)
    if a.cmd == "reopt":
        run_reopt(grids, times, a.limit)
    print(f"Bitti: {time.perf_counter() - t0:.0f} sn", flush=True)


if __name__ == "__main__":
    main()
