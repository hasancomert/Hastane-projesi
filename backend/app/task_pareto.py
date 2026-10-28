"""İş türü 'pareto' (Faz 6; şartname 7.8): açılış maliyeti – nüfus ağırlıklı ortalama mesafe ödünleşimi (λ taraması).

Erişim ağırlığı λ = PV · v · gidiş-dönüş · (c + zaman değeri / hız) kişi başı başvuru sayısı v'ye doğrusal bağlıdır;
v'yi f ile çarpmak λ'yı tam f katına çıkarır (backend/model/explain.py LAMBDA_KEY). Her f için toplam modunda çözüm:
noktada hastane sayısı, açılış maliyeti, ortalama yol, erişim maliyeti ve toplam maliyet ASIL λ ile (erişim / f). Asıl
toplamın en küçük olduğu nokta f = 1'dir (gap'ler içinde): mevcut optimum bu ödünleşimin dengesidir. Mevcut çözüm
(istekteki hospitals) f = 1 noktası olarak kullanılır ve işaretlenir. Varsayılan f değerleri 0,25 … 4 (12 nokta).

Sıcak başlangıç: f = 1'den yukarı, sonra aşağı; her nokta en yakın çözülmüş f'nin çözümünden (bu parametrelerle yeniden
değerlendirilerek) başlar. Önceden hesaplanmış sonuçlar: precomputed/pareto_*.json (pipeline/35_explanations.py);
aynı parametre ve kilitlerle istenen f'lerin hepsi dosyadaysa yanıt kuyruğa girmeden döner.
"""
from __future__ import annotations

import time

from pydantic import Field

from .config import engine_version
from .schemas import HospitalIn, LocksIn, ProblemIn
from . import task_explain as TE
from .task_explain import BaseBoundsIn, InnerProgress, bounds_payload, default_time_limit, hospitals_payload
from .tasks import ApiValidationError, JobContext, JobType, Prepared, register, stable_key, time_bucket
from .validation import cache_params, check_cells, resolve_problem_and_locks

DEFAULT_GAP = 2e-3


class ParetoIn(ProblemIn):
    locks: LocksIn = Field(default_factory=LocksIn)
    factors: list[float] | None = Field(None, min_length=2, max_length=30,
                                        description="λ çarpanları (0 < f ≤ 20); yoksa 0,25 … 4 (12 nokta); 1 her zaman eklenir")
    hospitals: list[HospitalIn] | None = Field(None, max_length=2000,
                                               description="Mevcut çözüm (f = 1 noktası; çözülmez, işaretlenir)")
    base_bounds: BaseBoundsIn | None = None
    time_limit: float | None = Field(None, gt=0, le=600, description="Nokta başına süre sınırı (sn)")
    gap: float | None = Field(None, ge=0, le=1)


def _factors(req: ParetoIn) -> list[float]:
    from backend.model.explain_common import PARETO_FACTORS
    fs = req.factors if req.factors else list(PARETO_FACTORS)
    return sorted({round(float(f), 6) for f in fs} | {1.0})


def _answer_from_file(rp, locks, fs: list[float]) -> dict | None:
    m = TE.match_precomputed("pareto", rp.params, locks)
    if m is None:
        return None
    path, doc = m
    by_f = {round(float(q["factor"]), 6): q for q in doc.get("points", [])}
    if not all(f in by_f for f in fs):
        return None
    res = {k: v for k, v in doc.items() if k not in ("points",)}
    res["points"] = [by_f[f] for f in fs]
    res["precomputed"] = {"name": path.stem, "generated_at": doc.get("generated_at"),
                          "generated_by": doc.get("generated_by"), "settings": doc.get("settings")}
    res["ok"], res["status"] = True, doc.get("status", "tamam")
    return res


def prepare(req: ParetoIn) -> Prepared:
    rp, locks = resolve_problem_and_locks(req, req.locks)
    km = rp.grid_km
    errors = check_cells([h.cell_id for h in (req.hospitals or [])], km, ["body", "hospitals"], ["cell_id"])
    if rp.params.objective_mode == "butce":
        errors.append({"loc": ["body", "mode"], "key": "solver.objective_mode", "type": "mod_uyumsuz",
                       "message_tr": "Pareto taraması toplam (ya da sabit p) modunda tanımlıdır."})
    fs = _factors(req)
    bad = [f for f in fs if not (0 < f <= 20)]
    if bad:
        errors.append({"loc": ["body", "factors"], "key": None, "type": "aralik_disi", "value": bad[:5],
                       "message_tr": "λ çarpanları 0 < f ≤ 20 aralığında olmalı."})
    if errors:
        raise ApiValidationError(errors, "Pareto isteği doğrulanamadı.")
    tl = float(req.time_limit) if req.time_limit is not None else default_time_limit(km)
    gap = float(req.gap) if req.gap is not None else DEFAULT_GAP
    hs = hospitals_payload(req.hospitals) if req.hospitals else None
    bb = bounds_payload(req, rp, locks) if hs else {}
    payload = {"grid_km": km, "overrides": rp.overrides, "locks": locks, "factors": fs, "hospitals": hs,
               "base_bounds": bb, "time_limit": tl, "gap": gap}
    key = stable_key("pareto", {"engine": engine_version(), "params": cache_params(rp.params), "locks": locks,
                                "factors": fs, "hospitals": sorted((h["cell_id"], h["class"] or "", h["beds"] or 0)
                                                                   for h in hs) if hs else None})
    variant = {"tl": time_bucket(tl), "gap": gap}
    pre = _answer_from_file(rp, locks, fs)
    if pre is not None and TE._CACHE is not None:
        try:
            if not TE._CACHE.has(key, dict(variant, pre=True)):
                TE._CACHE.put(key, dict(variant, pre=True), pre, job_type="pareto")
        except Exception:
            pass
    n_live = len(fs) - (1 if hs else 0)
    return Prepared(payload=payload, cache_key=key, variant=variant, max_runtime_s=n_live * (tl + 30.0) + 60.0,
                    warnings=rp.warnings, info={"grid_km": km, "points": len(fs), "time_limit_s": tl,
                                                "precomputed": pre is not None})


def run(ctx: JobContext, payload: dict) -> dict:
    from backend.model import explain as X
    from backend.model.params import Params
    from backend.model.solve import _jsonable

    T0 = time.perf_counter()
    fs = payload["factors"]

    outer = {"phase": "nokta", "phase_tr": "Pareto taraması", "done": 0, "total": len(fs)}

    def on_point(pt: dict, done: int, total: int) -> None:
        brief = {k: pt.get(k) for k in ("factor", "ok", "status", "n_hospitals", "opening_tl", "access_tl",
                                         "total_tl", "pop_weighted_mean_km", "gap", "source")}
        outer["done"] = done
        ctx.emit({"phase": "nokta", "phase_tr": f"λ × {X.fmt(pt['factor'], 2)} çözüldü", "done": done,
                  "total": total, "point": brief}, force=True)

    ctx.emit({"phase": "baslangic", "phase_tr": "Pareto taraması başlıyor", "done": 0, "total": len(fs)}, force=True)
    base = {"hospitals": payload["hospitals"], "bounds": payload.get("base_bounds")} if payload.get("hospitals") else None
    res = X.pareto(int(payload["grid_km"]), payload["overrides"], payload["locks"], fs,
                   time_limit=payload["time_limit"], gap=payload["gap"], base=base, cancel_event=ctx.cancel_event,
                   on_point=on_point, progress_cb=InnerProgress(ctx, outer))
    if ctx.cancelled():
        res["status"] = "durduruldu"
    ctx.emit({"phase": "bitti", "phase_tr": "Bitti", "done": len(res["points"]), "total": len(fs)}, force=True)
    res.update(ok=True, grid_km=int(payload["grid_km"]), params_hash=Params.load(payload["overrides"]).hash(),
               locks=payload["locks"], settings={"time_limit_s": payload["time_limit"], "gap": payload["gap"]},
               times={"total_s": time.perf_counter() - T0})
    return _jsonable(res)


def satisfies(c: dict, r: dict) -> bool:
    return bool(c.get("pre")) or (c.get("tl", 0) >= r.get("tl", 0) and c.get("gap", 1) <= r.get("gap", 1) + 1e-12)


def seed(cache) -> int:
    """Önceden hesaplanmış varsayılan Pareto sonuçları (her ızgara) varsayılan isteğin anahtarıyla önbelleğe."""
    TE.capture_cache(cache)
    return TE.seed_defaults("pareto", lambda km: prepare(ParetoIn(grid_km=km)))


register(JobType(
    name="pareto", request_model=ParetoIn, prepare=prepare, run=run,
    description_tr="Pareto eğrisi: erişim ağırlığı λ taraması → açılış maliyeti – ortalama mesafe (şartname 7.8).",
    satisfies=satisfies, seed=seed,
    cache_result=lambda res: bool(res.get("ok")) and res.get("status") == "tamam",
))
