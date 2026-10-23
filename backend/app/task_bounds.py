"""İş türü 'bounds': verilen parametrelerle 'En fazla kaç? / En az kaç?' kartları (şartname 4.5), süre sınırlı.

backend.model.bounds.bound_cards'ın adımları sırayla çalıştırılır (iptal HiGHS kesme geri çağrısıyla her adımın içinde
de etkilidir; kesilen adımın değeri kanıtlanmamış olarak raporlanır):
  1. kapasite sınırı + küme kapsama (model listeleri k ve R içindeki tüm adaylar) — süre sınırının %25'i + %25'i;
  2. gerçek p_min (tüm kısıtlarla en az hastane) — kalan süre. Kanıtlanamazsa alt sınır / bulunan değer raporlanır.
Varsayılan parametrelerin uzun süreli (kanıtlı) kartları GET /api/bounds/{km} ile hazır dosyadan okunur.
"""
from __future__ import annotations

import time

from .config import engine_version
from .schemas import BoundsIn
from .tasks import JobContext, JobType, Prepared, register, stable_key, time_bucket
from .validation import cache_params, resolve_problem_and_locks

LSCP_SHARE = 0.25          # süre sınırının küme kapsama (k listeleri) payı; R içindeki tüm adaylarla aynı pay daha
MIN_STEP_S = 1.0


def prepare(req: BoundsIn) -> Prepared:
    rp, locks = resolve_problem_and_locks(req, req.locks)
    tl = float(req.time_limit)
    payload = {"grid_km": rp.grid_km, "overrides": rp.overrides, "locks": locks, "time_limit": tl,
               "with_pmin": bool(req.with_pmin)}
    key = stable_key("bounds", {"engine": engine_version(), "params": cache_params(rp.params), "locks": locks})
    info = {"grid_km": rp.grid_km, "mode": rp.params.objective_mode, "time_limit_s": tl,
            "with_pmin": bool(req.with_pmin), "params_hash": rp.params.hash(), "overrides": rp.overrides}
    # k = 0 yapısının kurulumu (R içindeki tüm adaylar) süre sınırına dahil değildir: 2 km'de birkaç saniye
    return Prepared(payload=payload, cache_key=key, variant={"tl": time_bucket(tl), "pmin": bool(req.with_pmin)},
                    max_runtime_s=1.2 * tl + 30.0, warnings=rp.warnings, info=info)


def run(ctx: JobContext, payload: dict) -> dict:
    from backend.model import bounds as B
    from backend.model.instance import InfeasibleError, build_instance
    from backend.model.params import Params
    from backend.model.solve import _jsonable

    T0 = time.perf_counter()
    tl = float(payload["time_limit"])
    params = Params.load(payload["overrides"])
    ctx.emit({"phase": "ornek", "phase_tr": "Örnek kuruluyor"}, force=True)
    inst = build_instance(int(payload["grid_km"]), params, payload["locks"])
    base = {"grid_km": int(payload["grid_km"]), "mode": params.objective_mode, "params_hash": params.hash(),
            "overrides": dict(params.overrides), "locks": payload["locks"], "time_limit_s": tl,
            "settings": {"lscp_share": LSCP_SHARE, "with_pmin": payload["with_pmin"]}}
    try:
        inst.precheck()
    except InfeasibleError as e:
        return _jsonable({**base, "ok": False, "status": "olurlu_degil", "message_tr": str(e)})

    def left() -> float:
        return tl - (time.perf_counter() - T0)

    ctx.emit({"phase": "kapsama", "phase_tr": "Küme kapsama alt sınırı"}, force=True)
    lscp_t = max(MIN_STEP_S, LSCP_SHARE * tl)
    cards = B.bound_cards(inst, lscp_time=lscp_t, lscp_all_time=lscp_t, with_pmin=False,
                          cancel_event=ctx.cancel_event)
    status = "durduruldu" if ctx.cancelled() else "tamam"
    if payload["with_pmin"]:
        if ctx.cancelled():
            status = "durduruldu"
        else:
            ctx.emit({"phase": "p_min", "phase_tr": "Gerçek en az hastane sayısı (p_min)"}, force=True)
            ls_k = cards["coverage_lscp_k_list"]
            cap = cards["capacity"]
            pm = B.p_min(inst, time_limit=max(MIN_STEP_S, left()), cover_open=ls_k["open_pos"],
                         cancel_event=ctx.cancel_event)
            if ctx.cancelled():
                status = "durduruldu"
            # bound_cards ile aynı birleştirme: p_min'in alt sınırı = en büyük geçerli alt sınır
            lbs = [v for v in (pm["lower_bound"], ls_k["lower_bound"], cap["applies"], int(inst.lock_open.sum()),
                               inst.count_min) if v is not None]
            pm["lower_bound"] = max(lbs) if lbs else None
            if pm["value"] is not None and pm["lower_bound"] is not None and pm["lower_bound"] >= pm["value"]:
                pm["proven"] = True
            pm["k_nearest"] = int(inst.s.k_nearest)
            cards["p_min"] = pm
    total = time.perf_counter() - T0
    ctx.emit({"phase": "bitti", "phase_tr": "Bitti"}, force=True)
    return _jsonable({**base, **cards, "ok": True, "status": status, "time_s": total,
                      "message_tr": ("Sınır kartları hesaplandı." if status == "tamam" else
                                     "İptal edildi: kesilen adımların değerleri kanıtlanmamış; kapasite kartı "
                                     "kesin.")})


register(JobType(
    name="bounds", request_model=BoundsIn, prepare=prepare, run=run,
    description_tr="Sınır kartları: ızgaraya sığan en fazla hastane, kapasite ve kapsama alt sınırları, p_min.",
    cooperative_cancel=True,
    satisfies=lambda c, r: c.get("tl", 0) >= r.get("tl", 0) and (bool(c.get("pmin")) or not r.get("pmin")),
    rank=lambda c: (-float(c.get("tl", 0)),),
    cache_result=lambda res: bool(res.get("ok")) and res.get("status") == "tamam",
))
