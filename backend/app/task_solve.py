"""İş türü 'solve': canlı optimizasyon (backend.model.solve.solve), ilerleme akışı, önbellek, önceden çözülenler.

İlerleme olayları motorun iz olaylarından (phase / cozum / lagrange / butce_lagrange / incumbent / log / polish /
uyari) tek biçime indirgenir: {t, phase, phase_tr, kind, lb, ub, gap, …}. lb yalnız küresel geçerli alt sınırlardan
(Lagrange, bütçe Lagrange'ı, tam MILP'in dual sınırı) gelir; çekirdek MIP'in sınırı küresel olmadığı için alınmaz.
ub, denetimden geçip benimsenen çözümlerin (cozum / polish) raporlanan amacıdır. Kesin değerler sonuçtadır.
"""
from __future__ import annotations

import logging
import threading

import orjson

from backend.model.params import Params

from .config import PRECOMPUTED_DIR, RECOMMENDED_TIME_LIMIT_S, engine_version
from .schemas import SolveIn
from .tasks import JobContext, JobType, Prepared, register, stable_key, time_bucket
from .validation import cache_params, resolve_problem_and_locks

log = logging.getLogger("hastane.solve")

PHASE_TR = {
    "baslangic": "Başlatılıyor",
    "sezgisel": "Başlangıç çözümü (sezgisel)",
    "lagrange": "Alt sınır (Lagrange gevşetmesi)",
    "cekirdek_mip": "İyileştirme (LP yuvarlama, çekirdek MIP, pencere araması)",
    "mip": "Tam MILP (HiGHS dal-sınır)",
    "lagrange_2": "Alt sınır (Lagrange 2. evre)",
    "bitti": "Bitti",
}
NO_LOCKS = {"open": [], "closed": []}


def solve_key(params: Params, locks: dict) -> str:
    """Problem anahtarı: motor sürümü + etkili parametreler (ızgara, senaryo, mod dahil) + kilitler."""
    return stable_key("solve", {"engine": engine_version(), "params": cache_params(params),
                                "locks": {"open": sorted(locks.get("open", [])),
                                          "closed": sorted(locks.get("closed", []))}})


def default_time_limit(rp) -> float:
    """İstekte süre sınırı yoksa: overrides'ta solver.time_limit_s açıkça verildiyse o, değilse ızgaranın önerilen
    süresi (2 km 60 sn; Faz 4 incelemesi bulgu 4: 2 km'de 15 sn %1'e yetmiyor)."""
    if "solver.time_limit_s" in rp.overrides:
        return float(rp.params["solver.time_limit_s"])
    return float(RECOMMENDED_TIME_LIMIT_S.get(rp.grid_km, rp.params["solver.time_limit_s"]))


def prepare(req: SolveIn) -> Prepared:
    rp, locks = resolve_problem_and_locks(req, req.locks)
    tl = float(req.time_limit if req.time_limit is not None else default_time_limit(rp))
    gap = float(req.gap if req.gap is not None else rp.params["solver.mip_gap"])
    payload = {"grid_km": rp.grid_km, "overrides": rp.overrides, "locks": locks, "time_limit": tl, "gap": gap,
               "duals": bool(req.duals)}
    info = {"grid_km": rp.grid_km, "mode": rp.params.objective_mode, "scenario_year": rp.params.scenario_year,
            "time_limit_s": tl, "gap": gap, "locks": {"open": len(locks["open"]), "closed": len(locks["closed"])},
            "params_hash": rp.params.hash(), "overrides": rp.overrides}
    # bekçi: motorun sert süre sınırı ≈ T + 0,5 sn (Windows'ta alt süreç açılışı ≈1 sn); ilk çözümde yapı kurulumu
    return Prepared(payload=payload, cache_key=solve_key(rp.params, locks),
                    variant={"tl": time_bucket(tl), "gap": gap, "duals": bool(req.duals)},
                    max_runtime_s=1.1 * tl + 20.0, warnings=rp.warnings, info=info)


class _Progress:
    """Motorun iz olaylarını arayüz için tek biçime indirger (motor olayları kilitle sıralı gelir)."""

    def __init__(self, ctx: JobContext) -> None:
        self.ctx = ctx
        self.lb: float | None = None
        self.ub: float | None = None
        self.phase = "baslangic"
        self.lock = threading.Lock()

    def _gap(self) -> float | None:
        if self.lb is None or self.ub is None or self.ub == 0:
            return None
        return (self.ub - self.lb) / abs(self.ub)

    def __call__(self, ev: dict) -> None:
        kind = ev.get("kind")
        force = False
        with self.lock:
            if kind == "phase":
                self.phase = "sezgisel" if ev.get("phase") == "ornek" else str(ev.get("phase"))
                force = True
            elif kind in ("cozum", "polish"):
                p = ev.get("primal")
                if p is not None:
                    self.ub = p if self.ub is None else min(self.ub, p)
                force = True
            elif kind in ("lagrange", "butce_lagrange"):
                d = ev.get("dual", ev.get("lb"))
                if d is not None:
                    force = self.lb is None or bool(ev.get("final"))   # ilk ve evre sonu sınırı hemen gönderilir
                    self.lb = d if self.lb is None else max(self.lb, d)
            elif kind in ("incumbent", "log") and not ev.get("core"):
                d = ev.get("dual")
                if d is not None:
                    self.lb = d if self.lb is None else max(self.lb, d)
            elif kind == "uyari":
                force = True
            out = {"phase": self.phase, "phase_tr": PHASE_TR.get(self.phase, self.phase), "kind": kind,
                   "lb": self.lb, "ub": self.ub, "gap": self._gap()}
            for k in ("iter", "source", "message_tr", "nodes", "nu"):
                if ev.get(k) is not None:
                    out[k] = ev[k]
        self.ctx.emit(out, force=force)

    def start(self) -> None:
        self.ctx.emit({"phase": "baslangic", "phase_tr": PHASE_TR["baslangic"], "kind": "phase", "lb": None,
                       "ub": None, "gap": None}, force=True)

    def finish(self, res: dict) -> None:
        b = res.get("bounds") or {}
        self.ctx.flush()
        self.ctx.emit({"phase": "bitti", "phase_tr": PHASE_TR["bitti"], "kind": "bitti", "lb": b.get("lb_tl"),
                       "ub": b.get("ub_tl"), "gap": b.get("gap"), "status": res.get("status")}, force=True)


def run(ctx: JobContext, payload: dict) -> dict:
    import time

    from backend.model.instance import build_instance
    from backend.model.params import ParamError
    from backend.model.solve import solve
    prog = _Progress(ctx)
    prog.start()
    t0 = time.perf_counter()
    try:
        params = Params.load(payload["overrides"])
        inst = build_instance(int(payload["grid_km"]), params, payload["locks"])
    except ParamError as e:             # sunucu doğruladı; buraya düşmemeli
        inst = None
        err = str(e)
    t_inst = time.perf_counter() - t0
    if inst is None:
        res = solve(grid_km=payload["grid_km"], overrides=payload["overrides"], locks=payload["locks"],
                    time_limit=payload["time_limit"], gap=payload["gap"])
        res.setdefault("message_tr", err)
        prog.finish(res)
        return res
    n_seed = seed_engine(inst)          # gerekirse önceden çözülmüş çözümler başlangıç adayı olur (bkz. seed_engine)
    t_inst = time.perf_counter() - t0   # süre sınırı istekten itibaren sayılır (örnek kurulumu ve tohum dahil)
    res = solve(instance=inst, time_limit=max(0.5, payload["time_limit"] - t_inst), gap=payload["gap"],
                progress_cb=prog, cancel_event=ctx.cancel_event, compute_duals=payload["duals"])
    res["time_limit_s"] = payload["time_limit"]
    times = res.setdefault("times", {})
    times["instance_s"] = t_inst
    times["total_s"] = (times.get("total_s") or 0.0) + t_inst
    if n_seed:
        res["seeded_from_precomputed"] = n_seed
    prog.finish(res)
    return res


# ---------------------------------------------------------------------- önbellek kuralları
def satisfies(c: dict, r: dict) -> bool:
    """Önbellekteki kayıt isteği karşılar mı: en az aynı çaba (süre kovası ≥, hedef gap ≤) ya da kanıtlı olarak
    istenen gap içinde (status optimal, ulaşılan gap ≤ istenen); dual değerler istenmişse kayıtta olmalı."""
    if r.get("duals") and not c.get("duals"):
        return False
    if c.get("tl", 0) >= r.get("tl", 0) and c.get("gap", 1) <= r.get("gap", 1) + 1e-12:
        return True
    ach = c.get("ach")
    return c.get("status") == "optimal" and ach is not None and ach <= r.get("gap", 0) + 1e-12


def rank(c: dict) -> tuple:
    ach = c.get("ach")
    return (ach if ach is not None else 1.0, -float(c.get("tl", 0)))


def summarize(res: dict) -> dict:
    b = res.get("bounds") or {}
    return {"ach": b.get("gap"), "status": res.get("status"), "ub": b.get("ub_tl"), "lb": b.get("lb_tl"),
            "n": (res.get("kpis") or {}).get("n_hospitals")}


def cache_result(res: dict) -> bool:
    return bool(res.get("ok")) and bool((res.get("check") or {}).get("ok")) and \
        res.get("status") not in ("durduruldu", "hata")


def precomputed_files() -> list:
    """Önceden çözülmüş solve sonuçları: precomputed/default_{km}km.json ve scenario_<id>_{km}km.json."""
    out = [PRECOMPUTED_DIR / f"default_{km}km.json" for km in (2, 3, 5)]
    out += sorted(PRECOMPUTED_DIR.glob("scenario_*_*km.json"))
    return [p for p in out if p.exists()]


def seed(cache) -> int:
    """Önceden çözülmüş sonuçlar → önbellek (parametre özeti bugünkü kayıtla aynıysa)."""
    n = 0
    for path in precomputed_files():
        try:
            res = orjson.loads(path.read_bytes())
            params = Params.load(res.get("overrides") or {})
            if not (res.get("ok") and (res.get("check") or {}).get("ok")):
                continue
            if params.hash() != res.get("params_hash"):
                log.warning("%s: parametre özeti bugünkü kayıtla uyuşmuyor; önbelleğe eklenmedi", path.name)
                continue
            locks = res.get("locks") or NO_LOCKS
            key = solve_key(params, locks)
            variant = {"tl": time_bucket(float(res["time_limit_s"])), "gap": float(res["target_gap"]),
                       "duals": res.get("duals") is not None}
            variant.update(summarize(res))
            if not cache.has(key, variant):
                cache.put(key, variant, res, job_type="solve")
                n += 1
        except Exception as ex:
            log.warning("%s önbelleğe eklenemedi: %s", path.name, ex)
    return n


# ---------------------------------------------------------------------- işçide: motorun sıcak başlangıcı
# Faz 4 incelemesi bulgu 1: sunucu açıldıktan sonraki ilk canlı çözüm soğuktu (motorun önceki çözüm belleği boş) ve 3 km'de
# %1'e çoğu zaman 15 sn içinde ulaşamıyordu. Ölçüm (yeni süreç, cost.fixed_per_hospital = 300 milyon TL, 3 km, 15 sn,
# 2'şer çalıştırma): tohumsuz 2/2 'sure_doldu' (fark %1,07 / %1,08); varsayılan önceden çözülmüş çözümle tohumlu 2/2
# 'optimal', %1'e 3,9 sn'de. Tohum, motorun "aynı yapıdaki önceki çözümler" başlangıcıdır (solve.remember_solution;
# şartname 4.6): açık küme bu isteğin parametreleriyle yeniden değerlendirilir, yani sonuç tohumdan bağımsız olarak
# doğrudur — tohum yalnız başlangıçtır. Lagrange çarpanlarını önceden çözülmüş dual değerlerle tohumlamak denendi;
# motor ikisinden L'si büyük olanı seçiyor ve 4/4 çalıştırmada zayıf LP dualleri seçildi (fayda yok), bu yüzden yapılmadı.
_SEEDED: set = set()
_PRE_SOLS: list | None = None
SEED_PER_STRUCTURE = 2               # solve.SEED_MAX: motor yapı başına en çok 2 önceki çözümü yeniden değerlendirir


def _precomputed_solutions() -> list[dict]:
    """Önceden çözülmüş dosyalardan yalnız çözüm (hücre, sınıf, yatak) + yapı özeti (işçide bir kez okunur)."""
    global _PRE_SOLS
    if _PRE_SOLS is None:
        out = []
        for path in precomputed_files():
            try:
                r = orjson.loads(path.read_bytes())
                if not (r.get("ok") and (r.get("check") or {}).get("ok")):
                    continue
                out.append({"name": path.stem, "structure_hash": r.get("structure_hash"), "mode": r.get("mode"),
                            "overrides": r.get("overrides") or {}, "locks": r.get("locks") or NO_LOCKS,
                            "hospitals": [(h["cell_id"], h["class"], float(h["beds"])) for h in r["hospitals"]]})
            except Exception as ex:
                log.warning("%s okunamadı: %s", path.name, ex)
        _PRE_SOLS = out
    return _PRE_SOLS


def seed_engine(inst) -> int:
    """Motorun önceki çözüm belleğini önceden çözülmüş çözümlerle tohumlar (yalnız aynı yapıdakiler):
      * yapı için bellek boşsa: isteğin moduna ve parametrelerine en yakın SEED_PER_STRUCTURE çözüm (en yakını başta);
      * bellek doluysa ama içinde isteğin modunda çözüm yoksa (ör. toplam modundaki çözümlerden sonra ilk bütçe isteği):
        o moddaki en yakın önceden çözülmüş çözüm başa eklenir. Toplam modunun çözümleri bütçeye sığmadığı için bütçe
        isteğinde işe yaramaz (ölçüm: 5 km düşük bütçe senaryosu bu kural olmadan canlıda %13 farkla bitti).
    Döner: eklenen çözüm sayısı."""
    import numpy as np

    from backend.model import solve as S
    from backend.model.instance import Solution
    key = (inst.s.key, inst.nK, inst.nJ)
    modes = S.remembered_modes(inst)
    first = key not in _SEEDED and not modes
    _SEEDED.add(key)
    if not first and inst.mode in modes:
        return 0
    ov = {k: v for k, v in inst.params.overrides.items() if k != "grid.cell_km"}
    cands = []
    for e in _precomputed_solutions():
        if e["structure_hash"] != inst.s.key:
            continue
        eov = {k: v for k, v in e["overrides"].items() if k != "grid.cell_km"}
        diff = len({k for k in set(ov) | set(eov) if ov.get(k) != eov.get(k)})
        cands.append(((e["mode"] != inst.mode, diff, e["name"]), e))
    cands.sort(key=lambda c: c[0])
    if not first:
        cands = [c for c in cands if c[1]["mode"] == inst.mode][:1]
    pos = {str(c): j for j, c in enumerate(inst.s.cand_ids)}
    code = {c.code: k for k, c in enumerate(inst.classes)}
    n = 0
    for _, e in reversed(cands[:SEED_PER_STRUCTURE]):      # en yakın en son eklenir → listenin başında
        try:
            oj = np.array([pos[c] for c, _, _ in e["hospitals"]], dtype=np.int64)
            cls = np.array([code[k] for _, k, _ in e["hospitals"]], dtype=np.int64)
            beds = np.array([b for _, _, b in e["hospitals"]], dtype=float)
        except KeyError:
            continue
        o = np.argsort(oj)
        S.remember_solution(inst, Solution(oj[o], cls[o], beds[o], np.zeros(inst.nA), source=e["name"]))
        n += 1
    return n


def _warm_grid(km: int) -> None:
    from backend.model.instance import build_instance
    p = Params.load({"grid.cell_km": km})
    seed_engine(build_instance(km, p, None))


def warmup() -> list:
    """İşçide: motoru içe aktar, varsayılan ızgaranın yapısını kur ve önceden çözülmüş çözümlerle tohumla ('hazır'dan
    önce); diğer ızgaralar ertelenmiş adımlar olarak işçi boşken (2 km yapısı ≈1,4 sn)."""
    from backend.model.solve import solve  # noqa: F401
    d = int(Params.load()["grid.cell_km"])
    _warm_grid(d)
    return [lambda km=km: _warm_grid(km) for km in (5, 2, 3) if km != d]


register(JobType(
    name="solve", request_model=SolveIn, prepare=prepare, run=run,
    description_tr="Canlı optimizasyon: hastane sayısı, konumu ve boyutu (şartname 4.5–4.6).",
    satisfies=satisfies, rank=rank, summarize=summarize, cache_result=cache_result, seed=seed,
    warmup=warmup,
))
