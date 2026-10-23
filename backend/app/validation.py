"""Parametre ve kilit doğrulaması (sunucuda, işçiye gitmeden önce) → ApiValidationError (HTTP 422).

Kurallar:
  * Anahtar kayıtta (data/reference/parameters.json) yoksa: 'bilinmeyen parametre' (+ benzer anahtar önerisi).
  * Değer modelin tanım kümesi dışındaysa (backend/model/params.py SPEC: tür, alt/üst sınır, seçenekler): hata,
    izinli aralıkla birlikte.
  * Değer kaydırıcı aralığı ('range', arayüz tercihi) dışındaysa: hata; istek allow_outside_range=true taşıyorsa
    yalnız uyarı (motor da aralık dışını yalnız uyarı sayar: duyarlılık analizi aralık dışına çıkabilir).
  * Anahtarlar arası tutarlılık (ör. en az yatak > en çok yatak): Params.load'un Türkçe ParamError iletisi.
  * Kilit ve hastane hücre kimlikleri seçilen ızgarada olmalı.
"""
from __future__ import annotations

import difflib
import math
import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from backend.model import params as P

from .config import GRID_DIR, GRID_SIZES
from .schemas import LocksIn, ProblemIn
from .tasks import ApiValidationError

TOP_LEVEL = (("grid_km", "grid.cell_km"), ("scenario_year", "population.scenario_year"),
             ("mode", "solver.objective_mode"))
KIND_TR = {"f": "gerçel sayı", "i": "tamsayı", "b": "evet/hayır", "c": "seçenek", "p": "[[km, katsayı], …] listesi"}


def fmt_num(x: float) -> str:
    """Türkçe sayı biçimi (1.234.567,8)."""
    if isinstance(x, bool):
        return "evet" if x else "hayır"
    if isinstance(x, (int, np.integer)) or (isinstance(x, float) and x.is_integer() and abs(x) < 1e15):
        return f"{int(x):,}".replace(",", ".")
    s = f"{x:,.6g}"
    return s.replace(",", "_").replace(".", ",").replace("_", ".")


def allowed_info(key: str) -> dict | None:
    """Anahtarın tanım kümesi (JSON'a çevrilebilir): sayı → min/max/dahil mi; seçenek → options."""
    spec = P.SPEC.get(key)
    if spec is None:
        return None
    kind = spec[0]
    if kind == "b":
        return {"kind": "bool", "options": [True, False]}
    if kind == "c":
        return {"kind": "choice", "options": list(spec[1])}
    if kind == "p":
        return {"kind": "profile", "format": "[[kuş uçuşu km, katsayı], …] (km artan, katsayı > 0)"}
    lo, hi = spec[1], spec[2]
    return {"kind": "integer" if kind == "i" else "number", "min": lo if math.isfinite(lo) else None,
            "max": hi if math.isfinite(hi) else None, "min_inclusive": spec[3] if len(spec) > 3 else True,
            "max_inclusive": True}


def _domain_text(a: dict) -> str:
    if "options" in a:
        return "izinli değerler: " + ", ".join(fmt_num(o) if isinstance(o, (int, float)) else str(o)
                                                 for o in a["options"])
    if a.get("kind") == "profile":
        return "biçim: " + a["format"]
    parts = []
    if a.get("min") is not None:
        parts.append(f"{'≥' if a['min_inclusive'] else '>'} {fmt_num(a['min'])}")
    if a.get("max") is not None:
        parts.append(f"≤ {fmt_num(a['max'])}")
    return "izinli aralık: " + " ve ".join(parts) if parts else "sayı olmalı"


def json_safe(v: Any, depth: int = 0) -> Any:
    """Hata yanıtına konacak değer: JSON'a çevrilebilir ve küçük (64 bit dışı tamsayı, sonsuz/NaN metin olur; uzun
    liste/metin kısaltılır). orjson 64 bit dışı tamsayıyı yazamaz (Faz 4 incelemesi bulgu 7: 500 yerine 422)."""
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, int):
        return v if abs(v) < 2 ** 53 else str(v)[:40]
    if isinstance(v, float):
        return v if math.isfinite(v) else str(v)
    if isinstance(v, str):
        return v[:200]
    if isinstance(v, (list, tuple)) and depth < 3:
        return [json_safe(x, depth + 1) for x in list(v)[:20]]
    if isinstance(v, dict) and depth < 3:
        return {str(k)[:100]: json_safe(x, depth + 1) for k, x in list(v.items())[:20]}
    return str(v)[:200]


def _err(key: str | None, value: Any, message_tr: str, typ: str, loc: list, **extra) -> dict:
    out = {"loc": loc, "key": key, "message_tr": message_tr, "type": typ}
    if value is not None:
        out["value"] = json_safe(value)
    out.update(extra)
    return out


def check_value(key: str, value: Any, entry: dict, allow_outside_range: bool, loc: list
                ) -> tuple[dict | None, str | None]:
    """(hata, uyarı). Tanım kümesi motorun _coerce kurallarıyla aynıdır."""
    a = allowed_info(key)
    rng = entry.get("range") if isinstance(entry, dict) else None
    if a is None:
        return None, None
    kind = a["kind"]
    if kind == "bool":
        ok = isinstance(value, bool) or (isinstance(value, str) and value.lower() in
                                         ("true", "false", "evet", "hayır", "hayir"))
        if not ok:
            return _err(key, value, f"'{key}' evet/hayır (true/false) olmalı.", "tur_hatasi", loc, allowed=a), None
        return None, None
    if kind == "choice":
        opts = a["options"]
        v = value
        if isinstance(opts[0], int) and not isinstance(value, bool):
            try:
                v = int(value) if float(value) == int(float(value)) else value
            except (TypeError, ValueError, OverflowError):
                v = value
        if v not in opts or isinstance(value, bool):
            return _err(key, value, f"'{key}' için geçersiz seçenek; {_domain_text(a)}.", "secenek_disi", loc,
                        allowed=a), None
        return None, None
    if kind == "profile":
        try:
            prof = [(float(x), float(y)) for x, y in value]
            ok = bool(prof) and all(y > 0 and math.isfinite(x) and math.isfinite(y) for x, y in prof) and \
                all(b[0] > a_[0] for a_, b in zip(prof, prof[1:])) and len(prof) <= 100
        except (TypeError, ValueError, OverflowError):
            ok = False
        if not ok:
            return _err(key, value, f"'{key}' {a['format']} olmalı.", "tur_hatasi", loc, allowed=a), None
        return None, None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return _err(key, value, f"'{key}' sayı olmalı ({KIND_TR['i' if kind == 'integer' else 'f']}).",
                    "tur_hatasi", loc, allowed=a, ui_range=rng), None
    try:
        x = float(value)
    except OverflowError:                   # 1e308'den büyük tamsayı
        x = math.inf
    if not math.isfinite(x):
        return _err(key, value, f"'{key}' sonlu bir sayı olmalı.", "tur_hatasi", loc, allowed=a, ui_range=rng), None
    lo, hi = a.get("min"), a.get("max")
    if (lo is not None and (x < lo or (x == lo and not a["min_inclusive"]))) or (hi is not None and x > hi):
        return _err(key, value, f"'{key}' = {fmt_num(x)} modelin tanım kümesi dışında; {_domain_text(a)}.",
                    "aralik_disi", loc, allowed=a, ui_range=rng), None
    if kind == "integer" and abs(x - round(x)) > 1e-9:
        return _err(key, value, f"'{key}' tamsayı olmalı.", "tur_hatasi", loc, allowed=a, ui_range=rng), None
    if rng and not (rng[0] <= x <= rng[1]):
        txt = (f"'{key}' = {fmt_num(x)} arayüz aralığının [{fmt_num(rng[0])}; {fmt_num(rng[1])}] dışında")
        if allow_outside_range:
            return None, txt + " (allow_outside_range ile kabul edildi)."
        return _err(key, value, txt + "; aralık dışı değerler için allow_outside_range=true gönderin.",
                    "arayuz_araligi_disi", loc, allowed=a, ui_range=rng), None
    return None, None


@dataclass
class ResolvedProblem:
    overrides: dict
    params: P.Params
    grid_km: int
    warnings: list[str] = field(default_factory=list)


def resolve_problem(req: ProblemIn) -> ResolvedProblem:
    """Üst düzey alanlar (grid_km, scenario_year, mode) + overrides → doğrulanmış Params."""
    reg = P.registry()
    ov = dict(req.overrides)
    errors: list[dict] = []
    warnings: list[str] = []
    for fld, key in TOP_LEVEL:
        v = getattr(req, fld)
        if v is None:
            continue
        if key in ov and str(ov[key]) != str(v):
            errors.append(_err(key, ov[key], f"'{fld}' ({v}) ile overrides['{key}'] ({ov[key]}) çelişiyor.",
                               "celiski", ["body", fld]))
        ov[key] = v
    for k, v in ov.items():
        loc = ["body", "overrides", k]
        if k not in reg:
            close = difflib.get_close_matches(k, list(reg), n=3, cutoff=0.6)
            msg = f"Bilinmeyen parametre: '{k}'." + (f" Bunu mu demek istediniz: {', '.join(close)}?" if close else "")
            errors.append(_err(k, None, msg, "bilinmeyen_parametre", loc, suggestions=close))
            continue
        e, w = check_value(k, v, reg[k], req.allow_outside_range, loc)
        if e is not None:
            errors.append(e)
        if w is not None:
            warnings.append(w)
    if errors:
        raise ApiValidationError(errors, "Parametreler doğrulanamadı.")
    try:
        params = P.Params.load(ov)
    except P.ParamError as ex:
        msg = str(ex)
        m = re.search(r"'([A-Za-z0-9_.\-]+)'", msg)
        key = m.group(1) if m and m.group(1) in reg else None
        raise ApiValidationError([_err(key, ov.get(key) if key else None, msg, "tutarsiz_parametre",
                                       ["body", "overrides"] + ([key] if key else []))],
                                 "Parametreler birlikte geçerli değil.") from None
    # params.warnings yalnız kaydırıcı aralığı uyarılarıdır; yukarıda (Türkçe sayı biçimiyle) zaten üretildi
    return ResolvedProblem(overrides=ov, params=params, grid_km=int(params["grid.cell_km"]), warnings=warnings)


# ---------------------------------------------------------------------- hücre kimlikleri
_GRID_IDS: dict[int, frozenset] = {}


def grid_ids(km: int) -> frozenset:
    if km not in _GRID_IDS:
        if km not in GRID_SIZES:
            raise ValueError(km)
        with np.load(GRID_DIR / f"grid_{km}km.npz", allow_pickle=False) as z:
            _GRID_IDS[km] = frozenset(str(x) for x in z["id"].tolist())
    return _GRID_IDS[km]


def check_cells(cells: list[str], km: int, loc: list, suffix: list | None = None) -> list[dict]:
    """Izgarada olmayan hücre kimlikleri için hatalar; konum loc + [sıra] + suffix."""
    ids = grid_ids(km)
    errs = []
    for i, c in enumerate(cells):
        if c not in ids:
            hint = f"biçim '{km}km_<ix>_<iy>'" if not c.startswith(f"{km}km_") else "bu hücre il sınırı dışında"
            errs.append(_err(None, c, f"Bilinmeyen hücre kimliği ({km} km ızgarası): '{c}' ({hint}).",
                             "bilinmeyen_hucre", loc + [i] + (suffix or [])))
    return errs


def resolve_locks(locks: LocksIn, km: int) -> dict:
    errors = check_cells(locks.open, km, ["body", "locks", "open"]) + \
        check_cells(locks.closed, km, ["body", "locks", "closed"])
    both = sorted(set(locks.open) & set(locks.closed))
    if both:
        errors.append(_err(None, both[:10], f"Aynı hücre hem 'aç' hem 'yasak' kilitli: {', '.join(both[:10])}.",
                           "kilit_celiskisi", ["body", "locks"]))
    if errors:
        raise ApiValidationError(errors, "Kilitler doğrulanamadı.")
    return {"open": sorted(set(locks.open)), "closed": sorted(set(locks.closed))}


def resolve_problem_and_locks(req: ProblemIn, locks: LocksIn) -> tuple[ResolvedProblem, dict]:
    """Parametre ve kilit hatalarını birlikte toplar (istemci tüm hataları tek yanıtta görür)."""
    errors: list[dict] = []
    rp = None
    try:
        rp = resolve_problem(req)
        km = rp.grid_km
    except ApiValidationError as ex:
        errors += ex.errors
        km = req.grid_km or _grid_from(req.overrides)
    lk = None
    try:
        lk = resolve_locks(locks, km)
    except ApiValidationError as ex:
        errors += ex.errors
    if errors:
        raise ApiValidationError(errors, "İstek doğrulanamadı: parametreler ya da kilitler geçersiz.")
    return rp, lk


def _grid_from(overrides: dict) -> int:
    v = overrides.get("grid.cell_km")
    try:
        if int(v) in GRID_SIZES:
            return int(v)
    except (TypeError, ValueError, OverflowError):
        pass
    return int(P.registry()["grid.cell_km"]["value"])


def cache_params(params: P.Params) -> dict:
    """Önbellek anahtarına giren parametreler: etkili değerler, çözücü 'çaba' ayarları hariç (değişkeye girer)."""
    v = params.effective_values()
    v.pop("solver.time_limit_s", None)
    v.pop("solver.mip_gap", None)
    return v
