"""İstek modelleri (pydantic) ve doğrulama hatalarının Türkçeleştirilmesi.

Bilinmeyen alanlar reddedilir (extra='forbid'). Model parametreleri 'overrides' sözlüğünde, kayıttaki anahtarlarla
(data/reference/parameters.json) verilir; anahtar ve değer denetimi validation.py'dedir (422: anahtar + izinli aralık).
"""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ClientId = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:\-]+$")]
CellId = Annotated[str, Field(min_length=1, max_length=40)]
ClassCode = Literal["IV-C", "V-B", "V-C", "V-D"]


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class LocksIn(_Base):
    """Kilitler: 'open' hücrede hastane zorunlu, 'closed' yasak. Kimlik biçimi '<s>km_<ix>_<iy>'."""

    open: list[CellId] = Field(default_factory=list, max_length=2000)
    closed: list[CellId] = Field(default_factory=list, max_length=10000)


class JobOptions(_Base):
    client_id: ClientId | None = Field(None, description="Arayüz oturum kimliği ('en son istek kazanır' için)")
    replace: bool = Field(False, description="Aynı istemcinin aynı türdeki bekleyen/çalışan işini iptal et")
    use_cache: bool = Field(True, description="Önbellekte uygun sonuç varsa hemen döndür")


class ProblemIn(JobOptions):
    grid_km: Literal[2, 3, 5] | None = Field(None, description="Hücre boyutu (km); yoksa kayıttaki varsayılan")
    scenario_year: Literal[2025, 2030] | None = Field(None, description="Nüfus senaryosu")
    mode: Literal["toplam", "butce", "sabit_p"] | None = Field(None, description="Amaç modu")
    overrides: dict[str, Any] = Field(default_factory=dict, max_length=200,
                                      description="Parametre değişiklikleri: kayıt anahtarı → değer")
    allow_outside_range: bool = Field(False, description="Kaydırıcı aralığı dışındaki değerlere izin ver (uyarıyla)")


class SolveIn(ProblemIn):
    locks: LocksIn = Field(default_factory=LocksIn)
    time_limit: float | None = Field(None, gt=0, le=3600, description="Süre sınırı (sn); yoksa solver.time_limit_s")
    gap: float | None = Field(None, ge=0, le=1, description="Hedef göreli fark; yoksa solver.mip_gap")
    duals: bool = Field(True, description="Atama LP'sinin dual değerleri (gölge fiyatlar)")


class HospitalIn(_Base):
    cell_id: CellId
    class_: ClassCode | None = Field(None, alias="class", description="Boyut sınıfı; yoksa model seçer")
    beds: float | None = Field(None, gt=0, le=100000, description="Yatak sayısı; yoksa model seçer (sınıf aralığında)")


class EvaluateIn(ProblemIn):
    hospitals: list[HospitalIn] = Field(min_length=1, max_length=2000)
    duals: bool = True


class BoundsIn(ProblemIn):
    locks: LocksIn = Field(default_factory=LocksIn)
    time_limit: float = Field(20.0, gt=0, le=600, description="Toplam süre sınırı (sn): küme kapsama + p_min")
    with_pmin: bool = Field(True, description="Gerçek p_min (tüm kısıtlarla en az hastane) hesaplansın mı")


# ---------------------------------------------------------------------- Türkçe hata iletileri
_TR = {
    "missing": "Zorunlu alan eksik.",
    "extra_forbidden": "Bilinmeyen alan.",
    "literal_error": "Geçersiz değer; izinli değerler: {expected}.",
    "greater_than": "{gt} değerinden büyük olmalı.",
    "greater_than_equal": "{ge} ya da daha büyük olmalı.",
    "less_than": "{lt} değerinden küçük olmalı.",
    "less_than_equal": "{le} ya da daha küçük olmalı.",
    "int_parsing": "Tamsayı olmalı.",
    "int_type": "Tamsayı olmalı.",
    "int_from_float": "Tamsayı olmalı.",
    "float_parsing": "Sayı olmalı.",
    "float_type": "Sayı olmalı.",
    "bool_parsing": "true ya da false olmalı.",
    "bool_type": "true ya da false olmalı.",
    "string_type": "Metin olmalı.",
    "string_too_short": "En az {min_length} karakter olmalı.",
    "string_too_long": "En çok {max_length} karakter olmalı.",
    "string_pattern_mismatch": "Biçim geçersiz (izinli karakterler: harf, rakam, _ . : -).",
    "dict_type": "Nesne (anahtar → değer) olmalı.",
    "list_type": "Liste olmalı.",
    "too_short": "En az {min_length} öğe olmalı.",
    "too_long": "En çok {max_length} öğe olmalı.",
    "json_invalid": "Geçersiz JSON gövdesi.",
    "model_attributes_type": "Nesne (anahtar → değer) olmalı.",
    "finite_number": "Sonlu bir sayı olmalı.",
}


def translate_errors(errors: list[dict]) -> list[dict]:
    """pydantic/FastAPI hata listesi → Türkçe iletili liste (loc, field, message_tr, type, input, allowed)."""
    out = []
    for e in errors:
        typ = e.get("type", "")
        ctx = dict(e.get("ctx") or {})
        tmpl = _TR.get(typ)
        try:
            msg = tmpl.format(**{k: _fmt(v) for k, v in ctx.items()}) if tmpl else f"Geçersiz değer ({typ})."
        except (KeyError, IndexError):
            msg = tmpl or f"Geçersiz değer ({typ})."
        loc = [str(x) for x in e.get("loc", ())]
        item = {"loc": loc, "field": ".".join(loc[1:] if loc and loc[0] == "body" else loc), "message_tr": msg,
                "type": typ}
        if "input" in e and typ != "missing":
            item["input"] = _jsonable(e["input"])
        allowed = {k: _jsonable(v) for k, v in ctx.items() if k in ("gt", "ge", "lt", "le", "expected", "min_length",
                                                                    "max_length")}
        if allowed:
            item["allowed"] = allowed
        out.append(item)
    return out


def _fmt(v: Any) -> str:
    if isinstance(v, str):
        return v.replace(" or ", " ya da ")
    if isinstance(v, float):
        s = f"{v:,.6g}" if abs(v) < 1e15 else f"{v:g}"
        return s.replace(",", "_").replace(".", ",").replace("_", ".")
    return str(v)


def _jsonable(v: Any, depth: int = 0) -> Any:
    """Hata yanıtındaki 'input' değeri: JSON'a çevrilebilir ve küçük (validation.json_safe ile aynı kural)."""
    import math
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, int):
        return v if abs(v) < 2 ** 53 else str(v)[:40]
    if isinstance(v, float):
        return v if math.isfinite(v) else str(v)
    if isinstance(v, str):
        return v[:200]
    if isinstance(v, (list, tuple)) and depth < 3:
        return [_jsonable(x, depth + 1) for x in list(v)[:20]]
    if isinstance(v, dict) and depth < 3:
        return {str(k)[:100]: _jsonable(x, depth + 1) for k, x in list(v.items())[:20]}
    return str(v)[:200]
