"""Faz 2 — rapor için statik haritalar (PNG, ≈1600 px genişlik; projeksiyonda okunacak yazı boyutları).

Girdiler (hepsi git'te; ızgaralar pipeline/22_grids.py çıktısı)
  data/processed/grid/grid_{2,3,5}km.npz        hücre dizileri (kalibre nüfus, arazi örtüsü, aday kuralı)
  data/processed/grid/grid_3km_pieces.csv       (hücre, ilçe) parçaları — ilçe içi kaynak farkı için
  data/processed/grid/grid_summary.json         22_grids.py özeti — buradaki sayılarla birebir denetlenir
  data/processed/grid/ilce_kalibrasyon.csv      ilçe kalibrasyon katsayıları
  data/processed/ankara_il.geojson, ankara_ilceler.geojson, data/reference/kizilay.yaml, parameters.json

Çıktılar (docs/screenshots/)
  faz2_nufus_3km.png        TÜİK 2025'e kalibre nüfus yoğunluğu, 3 km (log ölçek) + merkez büyütmesi
  faz2_izgaralar.png        2 / 3 / 5 km yan yana: hücre başına nüfus (ortak renk ölçeği), il ve merkez görünümü
  faz2_adaylar_3km.png      aday hücreler J (varsayılan kural): nüfus ≥ P_min / yerleşime yakın (komşu hücre ≥
                            grid.candidate_near_density × 9 km²) / kapsama onarımı
  faz2_kaynak_farki_3km.png kalibre WorldPop − kalibre GHS-POP (hücre başına) ve ilçe içi uyuşmazlık payı

Yöntem notları
  Hücreler UTM 36N'de (EPSG:32636) kare çizilir ve il poligonuyla kırpılır. Aday sınıfları backend/model/grid_io.py
  kuralıyla yeniden hesaplanır (kapsama onarımı dahil) ve grid_summary.json ile karşılaştırılır; uyuşmazlıkta durur.
  İlçe içi uyuşmazlık payı D_ilçe = ½ Σ_hücre |WorldPop − GHS| / TÜİK2025_ilçe: iki kaynak da ilçe toplamında TÜİK'e
  eşit olduğundan D, ilçe nüfusunun "başka hücreye konmuş" payıdır (0 = aynı dağılım, 1 = hiç örtüşme yok).
  Renkler: sıralı tek ton (mavi), ıraksak mavi ↔ kırmızı + gri orta nokta, kategoriler renk körlüğüne dayanıklı ilk üç
  kategorik ton (doğrulayıcıdan geçti) + gri / tarama dokusu.

Çalıştırma (repo kökünden):  .venv/bin/python pipeline/24_static_maps.py
"""
from __future__ import annotations

import csv
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline import common  # noqa: E402
from backend.model import distance, grid_io  # noqa: E402

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import patheffects as pe  # noqa: E402
from matplotlib.collections import LineCollection, PatchCollection  # noqa: E402
from matplotlib.colors import BoundaryNorm, LinearSegmentedColormap, ListedColormap, LogNorm, SymLogNorm  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch, PathPatch, Rectangle  # noqa: E402
from matplotlib.path import Path as MplPath  # noqa: E402
from matplotlib.ticker import FixedLocator, FuncFormatter, NullLocator  # noqa: E402

OUT_DIR = common.ROOT / "docs" / "screenshots"
GRID_DIR = common.PROCESSED / "grid"
SUMMARY_JSON = GRID_DIR / "grid_summary.json"
CALIB_CSV = GRID_DIR / "ilce_kalibrasyon.csv"
TUIK_CSV = common.REFERENCE / "tuik_adnks_ankara.csv"
PARAMS_JSON = common.REFERENCE / "parameters.json"
IL_GEOJSON = common.PROCESSED / "ankara_il.geojson"
ILCE_GEOJSON = common.PROCESSED / "ankara_ilceler.geojson"
KIZILAY_YAML = common.REFERENCE / "kizilay.yaml"

CELL_SIZES_KM = (2, 3, 5)
DPI = 100
FIG_W = 16.0                      # 16 in × 100 dpi = 1600 px

# Merkez büyütme kutusu (UTM 36N, m): 7 merkez ilçenin sınır kutusu (x 455–514 km, y 4390–4452 km) + pay
ZOOM = (451_000.0, 4_391_000.0, 523_000.0, 4_451_000.0)
CENTRAL = ("altindag", "cankaya", "etimesgut", "kecioren", "mamak", "pursaklar", "yenimahalle")
ZOOM_LABEL_MIN_KM2 = 60.0         # büyütmede ilçe adı yalnız kutu içinde bu alandan büyükse yazılır
AVOID_MIN_SHARE = 0.4             # il haritası: ilçenin en az bu payı kutu dışındaysa ad dışarıdaki parçaya
# il haritasında kesikli kutu çizgisine binen adlar için elle kaydırma (km; yalnız yazı konumu)
LABEL_NUDGE_KM = {"elmadag": (12.5, -4.0), "kahramankazan": (0.0, 3.5)}

# ---------------------------------------------------------------- renk ve mürekkep (açık yüzey)
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK2 = "#52514e"
MUTED = "#898781"
HAIRLINE = "#e1e0d9"
LAND = "#f0efec"                  # nüfussuz / boş hücre zemini (ıraksak ölçeğin nötr ortası ile aynı)
# Sıralı tek ton (mavi 100 → 700)
BLUE_RAMP = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6",
             "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"]
# Kategorik ilk üç ton (tüm çiftlerde renk körlüğü ayrımı ≥ 9,2 ΔE; doğrulayıcı: dataviz validate_palette.js)
CAT_BLUE, CAT_ORANGE, CAT_AQUA = "#2a78d6", "#eb6834", "#1baf7a"
DIV_RED = "#e34948"               # ıraksak ölçeğin sıcak kutbu
GREY_POP = "#bcbab3"              # aday değil, nüfuslu
GREY_EMPTY = "#ecebe7"            # aday değil, nüfussuz

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 12, "axes.edgecolor": INK2, "text.color": INK,
    "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2, "savefig.facecolor": SURFACE,
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE,
})
HALO = [pe.withStroke(linewidth=3.2, foreground=SURFACE)]


# ---------------------------------------------------------------- OKLab (ıraksak ölçeğin kırmızı kolu için)
def _srgb_to_lin(c):
    c = np.asarray(c, float)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _lin_to_srgb(c):
    c = np.clip(np.asarray(c, float), 0, 1)
    return np.where(c <= 0.0031308, 12.92 * c, 1.055 * c ** (1 / 2.4) - 0.055)


def hex_to_oklab(h: str) -> np.ndarray:
    rgb = _srgb_to_lin([int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)])
    m1 = np.array([[0.4122214708, 0.5363325363, 0.0514459929], [0.2119034982, 0.6806995451, 0.1073969566],
                   [0.0883024619, 0.2817188376, 0.6299787005]])
    m2 = np.array([[0.2104542553, 0.7936177850, -0.0040720468], [1.9779984951, -2.4285922050, 0.4505937099],
                   [0.0259040371, 0.7827717662, -0.8086757660]])
    return m2 @ np.cbrt(m1 @ rgb)


def oklab_to_hex(lab: np.ndarray) -> str:
    m2i = np.array([[1.0, 0.3963377774, 0.2158037573], [1.0, -0.1055613458, -0.0638541728],
                    [1.0, -0.0894841775, -1.2914855480]])
    m1i = np.array([[4.0767416621, -3.3077115913, 0.2309699292], [-1.2684380046, 2.6097574011, -0.3413193965],
                    [-0.0041960863, -0.7034186147, 1.7076147010]])
    rgb = _lin_to_srgb(m1i @ (m2i @ lab) ** 3)
    return "#" + "".join(f"{int(round(v * 255)):02x}" for v in rgb)


def red_matching(l_target: float) -> str:
    """Kırmızı kutbun (DIV_RED) OKLab'da siyaha ya da nötr griye doğru karışımı; açıklık L hedefe eşit (ikiye bölme)."""
    red = hex_to_oklab(DIV_RED)
    other = np.zeros(3) if l_target < red[0] else hex_to_oklab(LAND)
    lo, hi = 0.0, 1.0
    for _ in range(50):
        t = (lo + hi) / 2
        lab = red + t * (other - red)
        if (lab[0] - l_target) * (other[0] - red[0]) < 0:
            lo = t
        else:
            hi = t
    return oklab_to_hex(red + (lo + hi) / 2 * (other - red))


def diverging_cmap() -> LinearSegmentedColormap:
    """Kırmızı (GHS fazla) ↔ gri ↔ mavi (WorldPop fazla); iki kol açıklıkta simetrik."""
    blue_arm = ["#104281", "#2a78d6", "#86b6ef", "#cde2fb"]          # koyu → açık
    red_arm = [red_matching(hex_to_oklab(b)[0]) for b in blue_arm]
    return LinearSegmentedColormap.from_list("kirmizi_gri_mavi", red_arm + [LAND] + blue_arm[::-1], N=256)


SEQ_CMAP = LinearSegmentedColormap.from_list("mavi_100_700", BLUE_RAMP, N=256)


# ---------------------------------------------------------------- biçim yardımcıları
def tr(x: float, d: int = 0) -> str:
    return common.fmt_tr(x, d)


def tr_signed(x: float) -> str:
    return ("−" if x < 0 else ("+" if x > 0 else "")) + tr(abs(x))


def pct_tr(x: float, d: int = 1) -> str:
    """0,345 -> '%34,5' (Türkçe yüzde yazımı)."""
    return "%" + tr(100 * x, d)


# ---------------------------------------------------------------- bağlam
@dataclass
class District:
    key: str
    name: str
    geom: object          # shapely, UTM 36N
    label_xy: tuple[float, float]


@dataclass
class Context:
    params: dict
    summary: dict
    kizilay: dict
    province: object      # shapely, UTM
    districts: list[District]
    grids: dict[int, grid_io.Grid]
    cand: dict[int, np.ndarray]          # kapsama onarımı dahil aday maskesi (varsayılan kaynak, 2025)
    added: dict[int, np.ndarray]         # onarımın eklediği hücre indisleri
    source: str
    rule: grid_io.RuleParams
    R_km: float
    tuik: list[dict]
    calib: list[dict]


def polylabel_xy(geom) -> tuple[float, float]:
    from shapely.ops import polylabel

    part = geom if geom.geom_type == "Polygon" else max(geom.geoms, key=lambda p: p.area)
    p = polylabel(part, tolerance=200.0)
    return float(p.x), float(p.y)


def load_context() -> Context:
    import geopandas as gpd
    import shapely
    import yaml

    params = grid_io.load_params(PARAMS_JSON)
    summary = json.loads(SUMMARY_JSON.read_text(encoding="utf-8"))
    kizilay = yaml.safe_load(KIZILAY_YAML.read_text(encoding="utf-8"))
    il = gpd.read_file(IL_GEOJSON).to_crs(common.CRS_UTM36N)
    province = shapely.union_all(il.geometry.values)
    dd = gpd.read_file(ILCE_GEOJSON).to_crs(common.CRS_UTM36N)
    districts = [District(r["ilce_key"], r["ilce"], r.geometry, polylabel_xy(r.geometry)) for _, r in dd.iterrows()]
    with open(TUIK_CSV, encoding="utf-8") as f:
        tuik = list(csv.DictReader(f))
    with open(CALIB_CSV, encoding="utf-8") as f:
        calib = list(csv.DictReader(f))

    src = grid_io.source_key(params["population.grid_source"]["value"])
    rule = grid_io.RuleParams.from_params(params)
    R = float(params["access.max_distance_km"]["value"])
    circ = distance.Circuity.from_params(params)
    grids, cand, added = {}, {}, {}
    for s in CELL_SIZES_KM:
        g = grid_io.load_grid(s, GRID_DIR)
        base = grid_io.candidate_mask(g, rule, src, 2025)
        if not np.array_equal(base, g.candidate):
            raise SystemExit(f"{s} km: npz 'candidate' dizisi aday kuralıyla uyuşmuyor — önce pipeline/22_grids.py")
        rep = grid_io.coverage_repair(g, base, rule, R, circ, src, 2025)
        grids[s], cand[s], added[s] = g, rep.mask, rep.added
        # 22_grids.py özetiyle birebir denetim
        ss = summary["sizes"][f"{s}km"]
        cov = ss["coverage_default"]
        got = {"n_cells": g.n, "n_demand": int(grid_io.demand_mask(g, src, 2025).sum()), "J_rule": int(base.sum()),
               "J_repaired": int(rep.mask.sum()), "added": [str(g.id[i]) for i in rep.added]}
        want = {"n_cells": ss["n_cells"], "n_demand": ss["n_demand_cells"][f"{src}_2025"], "J_rule": cov["J_rule"],
                "J_repaired": cov["J_repaired"], "added": cov["added_cell_ids"]}
        if got != want:
            raise SystemExit(f"{s} km: grid_summary.json ile uyuşmazlık: {got} ≠ {want}")
        if int(g.pop(src, 2025).sum()) != int(params["population.province_2025"]["value"]):
            raise SystemExit(f"{s} km: il toplamı TÜİK 2025 ile uyuşmuyor")
    return Context(params, summary, kizilay, province, districts, grids, cand, added, src, rule, R, tuik, calib)


# ---------------------------------------------------------------- çizim yardımcıları
def geom_path(geom) -> MplPath:
    """shapely (Multi)Polygon -> matplotlib bileşik yol (delikler dahil)."""
    polys = [geom] if geom.geom_type == "Polygon" else list(geom.geoms)
    verts, codes = [], []
    for p in polys:
        for ring in (p.exterior, *p.interiors):
            xy = np.asarray(ring.coords)
            verts.append(xy)
            c = np.full(len(xy), MplPath.LINETO, dtype=np.uint8)
            c[0] = MplPath.MOVETO
            c[-1] = MplPath.CLOSEPOLY
            codes.append(c)
    return MplPath(np.concatenate(verts), np.concatenate(codes))


def boundary_segments(geom) -> list[np.ndarray]:
    polys = [geom] if geom.geom_type == "Polygon" else list(geom.geoms)
    return [np.asarray(r.coords) for p in polys for r in (p.exterior, *p.interiors)]


def cell_raster(g: grid_io.Grid, values: np.ndarray):
    """Hücre değerlerini düzenli ızgaraya yerleştirir: (x kenarları, y kenarları, maskeli 2B dizi)."""
    s = g.cell_km * 1000.0
    ix0, iy0 = int(g.ix.min()), int(g.iy.min())
    W, H = int(g.ix.max()) - ix0 + 1, int(g.iy.max()) - iy0 + 1
    arr = np.full((H, W), np.nan)
    arr[g.iy - iy0, g.ix - ix0] = values
    X = (ix0 + np.arange(W + 1)) * s
    Y = (iy0 + np.arange(H + 1)) * s
    return X, Y, np.ma.masked_invalid(arr)


def clip_to(ax, artist, path: MplPath) -> None:
    patch = PathPatch(path, transform=ax.transData, fc="none", ec="none")
    artist.set_clip_path(patch)


def base_axes(ax, extent) -> None:
    xmin, ymin, xmax, ymax = extent
    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)
    ax.set_aspect("equal", adjustable="datalim")
    ax.set_xticks([])
    ax.set_yticks([])
    for sp in ax.spines.values():
        sp.set_visible(False)


def draw_land(ax, ctx: Context, path: MplPath) -> None:
    ax.add_patch(PathPatch(path, fc=LAND, ec="none", zorder=1))


def draw_cells(ax, g, values, cmap, norm, path, edge_lw=0.0, edge_color=SURFACE, zorder=2):
    X, Y, A = cell_raster(g, values)
    qm = ax.pcolormesh(X, Y, A, cmap=cmap, norm=norm, shading="flat", zorder=zorder,
                       edgecolors=edge_color if edge_lw > 0 else "none", linewidth=edge_lw, antialiased=edge_lw > 0)
    clip_to(ax, qm, path)
    return qm


def draw_boundaries(ax, ctx: Context, *, district_lw=0.8, province_lw=1.8, district_color=INK2) -> None:
    segs = [s for d in ctx.districts for s in boundary_segments(d.geom)]
    lc = LineCollection(segs, colors=district_color, linewidths=district_lw, zorder=5,
                        path_effects=[pe.withStroke(linewidth=district_lw + 1.8, foreground=SURFACE)])
    ax.add_collection(lc)
    ax.add_collection(LineCollection(boundary_segments(ctx.province), colors=INK, linewidths=province_lw, zorder=6))


def draw_kizilay(ax, ctx: Context, *, ms=17, label=True, fontsize=13, offset=(9, 7)) -> None:
    x, y = ctx.kizilay["x_utm36n"], ctx.kizilay["y_utm36n"]
    ax.plot(x, y, marker="*", ms=ms, color=INK, mec=SURFACE, mew=1.4, zorder=9, ls="")
    if label:
        ax.annotate("Kızılay", (x, y), xytext=offset, textcoords="offset points", fontsize=fontsize,
                    fontweight="bold", color=INK, zorder=9, path_effects=HALO)


def draw_district_names(ax, ctx: Context, *, skip=(), fontsize=11.5, box=None, min_km2=0.0, avoid=None) -> None:
    """İlçe adları polylabel noktasında. box verilirse (büyütme) ilçe ∩ kutu parçasında; avoid verilirse (il haritası)
    ilçenin yeterli kısmı kutu dışındaysa ad dışarıdaki parçaya yazılır (kesikli kutu çizgisiyle çakışmasın)."""
    import shapely

    for d in ctx.districts:
        if d.key in skip:
            continue
        x, y = d.label_xy
        if box is not None:
            part = shapely.intersection(d.geom, shapely.box(*box))
            if part.is_empty or part.area / 1e6 < min_km2:
                continue
            x, y = polylabel_xy(part)
        elif avoid is not None:
            out = shapely.difference(d.geom, shapely.box(*avoid))
            if not out.is_empty and out.area >= AVOID_MIN_SHARE * d.geom.area:
                x, y = polylabel_xy(out)
            dx, dy = LABEL_NUDGE_KM.get(d.key, (0.0, 0.0))
            x, y = x + dx * 1000.0, y + dy * 1000.0
        ax.text(x, y, d.name, ha="center", va="center", fontsize=fontsize, color=INK, zorder=8,
                path_effects=HALO, clip_on=True)


def scale_bar(ax, x0, y0, length_km, *, fontsize=11, height=None) -> None:
    L = length_km * 1000.0
    h = height if height is not None else L / 25
    half = L / 2
    ax.add_patch(Rectangle((x0, y0), half, h, fc=INK, ec=INK, lw=0.8, zorder=9))
    ax.add_patch(Rectangle((x0 + half, y0), half, h, fc=SURFACE, ec=INK, lw=0.8, zorder=9))
    for xx, t in ((x0, "0"), (x0 + half, tr(length_km / 2)), (x0 + L, f"{tr(length_km)} km")):
        ax.text(xx, y0 - h * 0.6, t, ha="center", va="top", fontsize=fontsize, color=INK2, zorder=9,
                path_effects=HALO)


def zoom_frame(ax, *, label: str | None = None, fontsize=12) -> None:
    xmin, ymin, xmax, ymax = ZOOM
    ax.add_patch(Rectangle((xmin, ymin), xmax - xmin, ymax - ymin, fill=False, ec=INK, lw=1.6, ls=(0, (5, 3)),
                           zorder=10))
    if label:
        ax.text(xmin, ymax + 2500, label, ha="left", va="bottom", fontsize=fontsize, color=INK, zorder=10,
                path_effects=HALO)


def frame_axes(ax) -> None:
    for sp in ax.spines.values():
        sp.set_visible(True)
        sp.set_edgecolor(INK)
        sp.set_linewidth(1.6)
        sp.set_linestyle((0, (5, 3)))


def province_extent(ctx: Context, pad_km=4.0):
    xmin, ymin, xmax, ymax = ctx.province.bounds
    p = pad_km * 1000.0
    return (xmin - p, ymin - p, xmax + p, ymax + p)


def log_ticks(vmin: float, vmax: float) -> list[float]:
    lo, hi = math.ceil(math.log10(vmin)), math.floor(math.log10(vmax))
    return [10.0 ** e for e in range(lo, hi + 1)]


def header(fig, title: str, subtitle: str) -> None:
    H = fig.get_figheight()
    fig.text(0.012, 1 - 0.24 / H, title, ha="left", va="top", fontsize=21, fontweight="bold", color=INK)
    fig.text(0.012, 1 - 0.70 / H, subtitle, ha="left", va="top", fontsize=13, color=INK2, linespacing=1.35)


def footer(fig, text: str) -> None:
    fig.text(0.012, 0.008, text, ha="left", va="bottom", fontsize=10, color=INK2)


def save(fig, name: str) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / name
    fig.savefig(path, dpi=DPI, metadata={"Software": None})
    plt.close(fig)
    return path


def density(g: grid_io.Grid, pop: np.ndarray) -> np.ndarray:
    """kişi / km² (hücrenin il içindeki alanına göre); nüfussuz hücre NaN."""
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(pop > 0, pop / np.maximum(g.area_km2, 1e-9), np.nan)


def district_name(ctx: Context, g: grid_io.Grid, i: int) -> str:
    return g.district_names[int(g.district[i])]


SOURCE_LINE = ("Kaynak: TÜİK ADNKS 2025; WorldPop R2025A, GHS-POP R2023A (100 m); ESA WorldCover 2021; "
               "geoBoundaries; Kızılay: OSM. UTM 36N. Üreten: pipeline/22_grids.py, pipeline/24_static_maps.py")
ZOOM_HANDLE = Line2D([], [], color=INK, lw=1.6, ls=(0, (5, 3)), label="kesikli kutu: büyütülen merkez")


def nice_ceil(v: float) -> float:
    """1-2-3-5 dizisinde v'den büyük ya da eşit en küçük değer (renk ölçeği üst sınırı)."""
    e = 10 ** math.floor(math.log10(v))
    return next(m * e for m in (1, 2, 3, 5, 10) if m * e >= v)


# ---------------------------------------------------------------- 1) nüfus yoğunluğu, 3 km
def fig_density(ctx: Context) -> tuple[Path, dict]:
    s = 3
    g = ctx.grids[s]
    pop = g.pop(ctx.source, 2025)
    dens = density(g, pop)
    P = int(pop.sum())
    vmax = nice_ceil(float(np.nanmax(dens)))
    norm = LogNorm(vmin=1.0, vmax=vmax)
    path = geom_path(ctx.province)

    H = 10.4
    fig = plt.figure(figsize=(FIG_W, H), dpi=DPI)
    src_name = "WorldPop R2025A" if ctx.source == "wp" else "GHS-POP R2023A"
    header(fig, f"Ankara nüfus yoğunluğu, 3 km ızgara — il toplamı {tr(P)} kişi (TÜİK ADNKS 2025)",
           f"Hücre nüfusu: {src_name} 100 m dağılımı, her ilçede TÜİK 2025 toplamına ölçeklendi (dasimetrik kalibrasyon).\n"
           "Renk: kişi / km², hücrenin il içindeki alanına göre (log ölçek).")
    ax = fig.add_axes((0.005, 0.045, 0.585, 0.80))
    base_axes(ax, province_extent(ctx))
    draw_land(ax, ctx, path)
    qm = draw_cells(ax, g, dens, SEQ_CMAP, norm, path, edge_lw=0.25)
    draw_boundaries(ax, ctx)
    draw_district_names(ax, ctx, skip=CENTRAL, avoid=ZOOM)
    zoom_frame(ax)
    draw_kizilay(ax, ctx, ms=15, label=False)
    xmin, ymin, xmax, ymax = ctx.province.bounds
    scale_bar(ax, xmin + 2_000, ymin + 6_000, 50)

    # sağ sütun: merkez büyütmesi
    zx = ZOOM[2] - ZOOM[0]
    zy = ZOOM[3] - ZOOM[1]
    zw = 0.385
    zh = zw * FIG_W / H * zy / zx
    axz = fig.add_axes((0.605, 0.855 - zh, zw, zh))
    base_axes(axz, ZOOM)
    frame_axes(axz)
    draw_land(axz, ctx, path)
    draw_cells(axz, g, dens, SEQ_CMAP, norm, path, edge_lw=0.6)
    draw_boundaries(axz, ctx, district_lw=1.1)
    draw_district_names(axz, ctx, box=ZOOM, min_km2=ZOOM_LABEL_MIN_KM2, fontsize=12.5)
    draw_kizilay(axz, ctx, ms=20)
    scale_bar(axz, ZOOM[0] + 2_500, ZOOM[1] + 3_000, 10, height=500)

    cax = fig.add_axes((0.62, 0.855 - zh - 0.08, 0.355, 0.022))
    cb = fig.colorbar(qm, cax=cax, orientation="horizontal", extend="min")
    cb.locator = FixedLocator(log_ticks(1.0, vmax))
    cb.formatter = FuncFormatter(lambda v, _: tr(v))
    cb.update_ticks()
    cb.ax.xaxis.set_minor_locator(NullLocator())
    cb.ax.tick_params(labelsize=12)
    cb.outline.set_edgecolor(MUTED)
    cb.set_label("kişi / km² (log ölçek; < 1 kişi/km² en açık tonda)", fontsize=12.5, color=INK2)

    n_dem = int((pop > 0).sum())
    imax = int(np.argmax(pop))
    cal = [float(r[f"{ctx.source}_kalibrasyon_katsayisi"]) for r in ctx.calib]
    stats = {"n_cells": g.n, "n_demand": n_dem, "n_empty": g.n - n_dem,
             "n_fully_inside": int((g.inside_frac >= 1 - 1e-12).sum()),
             "max_cell_pop": int(pop[imax]), "max_cell_dens": float(dens[imax]), "max_cell_district": district_name(ctx, g, imax),
             "max_dens": float(np.nanmax(dens)), "below_1": int(np.sum(dens < 1)),
             "factor_min": min(cal), "factor_max": max(cal)}
    lines = [
        f"• {tr(g.n)} hücre il sınırıyla kesişiyor ({tr(stats['n_fully_inside'])} tanesi tamamen il içinde)",
        f"• Talep hücresi (nüfus > 0): {tr(n_dem)}  ·  nüfussuz (gri): {tr(stats['n_empty'])}",
        f"• En kalabalık hücre: {tr(stats['max_cell_pop'])} kişi ({stats['max_cell_district']}),"
        f" {tr(stats['max_cell_dens'])} kişi/km²",
        f"• İlçe kalibrasyon katsayısı (TÜİK ÷ {src_name.split()[0]}): {tr(min(cal), 3)} – {tr(max(cal), 3)}",
        "• Hücre: UTM 36N'de 3 × 3 km kare; il sınırında kırpıldı",
    ]
    fig.text(0.62, 0.855 - zh - 0.165, "\n".join(lines), ha="left", va="top", fontsize=12.5, color=INK,
             linespacing=1.55)
    ax.legend(handles=[Patch(fc=LAND, ec=MUTED, lw=0.6, label="nüfussuz hücre"),
                       Line2D([], [], marker="*", ls="", color=INK, mec=SURFACE, ms=15, label="Kızılay"), ZOOM_HANDLE],
              loc="upper left", frameon=False, fontsize=12.5, borderaxespad=0.2)
    footer(fig, SOURCE_LINE)
    return save(fig, "faz2_nufus_3km.png"), stats


# ---------------------------------------------------------------- 2) 2 / 3 / 5 km yan yana
def fig_grids(ctx: Context) -> tuple[Path, dict]:
    path = geom_path(ctx.province)
    vmax = 10 ** math.ceil(math.log10(max(int(ctx.grids[s].pop(ctx.source, 2025).max()) for s in CELL_SIZES_KM)))
    norm = LogNorm(vmin=1.0, vmax=vmax)
    ext = province_extent(ctx, pad_km=3.0)
    # yerleşim inç cinsinden, yukarıdan aşağı
    left_in, gap_in = 0.19, 0.19
    col_in = (FIG_W - 2 * left_in - 2 * gap_in) / 3
    top_in = col_in * (ext[3] - ext[1]) / (ext[2] - ext[0])
    zoom_in = col_in * (ZOOM[3] - ZOOM[1]) / (ZOOM[2] - ZOOM[0])
    y_coltitle, y_map = 1.42, 2.02
    H = y_map + top_in + 0.2 + zoom_in + 0.3 + 0.22 + 0.62 + 0.32
    fy = lambda inch: 1 - inch / H  # noqa: E731  (yukarıdan inç → şekil kesri)
    fig = plt.figure(figsize=(FIG_W, H), dpi=DPI)
    header(fig, "Üç ızgara: 2 km, 3 km, 5 km — hücre başına nüfus (TÜİK 2025'e kalibre, ortak renk ölçeği)",
           "Üst sıra: il geneli. Alt sıra: merkez ilçeler (kesikli kutu); hücre kenarları görünür.\n"
           "|J| = varsayılan kurala göre aday hücre sayısı (kapsama onarımı dahil) = ızgaraya sığabilecek en fazla hastane.")
    stats = {}
    qm = None
    for c, s in enumerate(CELL_SIZES_KM):
        g = ctx.grids[s]
        pop = g.pop(ctx.source, 2025).astype(float)
        vals = np.where(pop > 0, pop, np.nan)
        n_dem = int((pop > 0).sum())
        J = int(ctx.cand[s].sum())
        stats[f"{s}km"] = {"n_cells": g.n, "n_demand": n_dem, "J": J, "J_rule": int(g.candidate.sum()),
                           "added": len(ctx.added[s]), "max_cell_pop": int(pop.max())}
        x0 = (left_in + c * (col_in + gap_in)) / FIG_W
        w = col_in / FIG_W
        ax = fig.add_axes((x0, fy(y_map + top_in), w, top_in / H))
        base_axes(ax, ext)
        draw_land(ax, ctx, path)
        qm = draw_cells(ax, g, vals, SEQ_CMAP, norm, path)
        draw_boundaries(ax, ctx, district_lw=0.5, province_lw=1.3)
        zoom_frame(ax)
        draw_kizilay(ax, ctx, ms=11, label=False)
        fig.text(x0 + 0.004, fy(y_coltitle), f"{s} km ızgara ({s * s} km² hücre)", ha="left", va="top", fontsize=16,
                 fontweight="bold", color=INK)
        rep = f" (+{len(ctx.added[s])} onarım)" if len(ctx.added[s]) else ""
        fig.text(x0 + 0.004, fy(y_coltitle + 0.32), f"{tr(g.n)} hücre · {tr(n_dem)} talep hücresi · |J| = {tr(J)}{rep}",
                 ha="left", va="top", fontsize=12.5, color=INK)
        y_zoom_top = y_map + top_in + 0.2
        axz = fig.add_axes((x0, fy(y_zoom_top + zoom_in), w, zoom_in / H))
        base_axes(axz, ZOOM)
        frame_axes(axz)
        draw_land(axz, ctx, path)
        draw_cells(axz, g, vals, SEQ_CMAP, norm, path, edge_lw={2: 0.35, 3: 0.5, 5: 0.7}[s])
        draw_boundaries(axz, ctx, district_lw=0.6, province_lw=1.3, district_color=INK)
        draw_kizilay(axz, ctx, ms=14, fontsize=11.5)
        scale_bar(axz, ZOOM[0] + 2_500, ZOOM[1] + 3_000, 10, fontsize=10, height=550)
    y_cb = y_map + top_in + 0.2 + zoom_in + 0.3
    cax = fig.add_axes((0.33, fy(y_cb + 0.22), 0.5, 0.22 / H))
    cb = fig.colorbar(qm, cax=cax, orientation="horizontal")
    cb.locator = FixedLocator(log_ticks(1.0, vmax))
    cb.formatter = FuncFormatter(lambda v, _: tr(v))
    cb.update_ticks()
    cb.ax.xaxis.set_minor_locator(NullLocator())
    cb.ax.tick_params(labelsize=12)
    cb.outline.set_edgecolor(MUTED)
    cb.set_label("kişi / hücre (log ölçek; aynı yoğunlukta 5 km hücrede 2 km hücrenin 6,25 katı kişi olur)",
                 fontsize=12.5, color=INK2)
    fig.legend(handles=[Patch(fc=LAND, ec=MUTED, lw=0.6, label="nüfussuz hücre"),
                        Line2D([], [], marker="*", ls="", color=INK, mec=SURFACE, ms=14, label="Kızılay"), ZOOM_HANDLE],
               loc="upper left", bbox_to_anchor=(left_in / FIG_W, fy(y_cb - 0.12)), frameon=False, fontsize=12.5,
               labelspacing=0.35)
    footer(fig, SOURCE_LINE)
    return save(fig, "faz2_izgaralar.png"), stats


# ---------------------------------------------------------------- 3) aday hücreler, 3 km
CAT_LABELS = {
    1: "aday — nüfus ≥ P_min",
    2: "aday — yerleşime yakın",
    3: "aday — kapsama onarımı",
    4: "aday değil — nüfuslu",
    5: "aday değil — nüfussuz",
    6: "uygun değil — su + sulak alan ≥ eşik",
    7: "uygun değil — il içi payı < eşik",
}


def candidate_classes(ctx: Context, s: int) -> np.ndarray:
    g = ctx.grids[s]
    pop = g.pop(ctx.source, 2025)
    r = ctx.rule
    cls = np.zeros(g.n, dtype=np.int64)
    inside_low = g.inside_frac < r.min_inside
    wf = g.water_frac
    water = ~inside_low & ~(np.isfinite(wf) & (wf < r.max_water))
    cand = ctx.cand[s]
    added = np.zeros(g.n, dtype=bool)
    added[ctx.added[s]] = True
    by_pop = cand & ~added & (pop >= r.min_pop)
    by_near = cand & ~added & ~by_pop
    cls[inside_low] = 7
    cls[water] = 6
    rest = ~inside_low & ~water & ~cand
    cls[rest & (pop > 0)] = 4
    cls[rest & (pop == 0)] = 5
    cls[by_pop] = 1
    cls[by_near] = 2
    cls[added] = 3
    assert (cls > 0).all()
    return cls


def fig_candidates(ctx: Context) -> tuple[Path, dict]:
    s = 3
    g = ctx.grids[s]
    cls = candidate_classes(ctx, s)
    counts = {k: int((cls == k).sum()) for k in CAT_LABELS}
    J = int(ctx.cand[s].sum())
    assert counts[1] + counts[2] + counts[3] == J
    r = ctx.rule
    near_thr = r.near_threshold(g.cell_km)       # kişi / hücre = yoğunluk eşiği × s²
    if near_thr != ctx.summary["sizes"][f"{s}km"]["J_table"]["near_threshold_per_cell"]:
        raise SystemExit("yakınlık eşiği grid_summary.json ile uyuşmuyor — önce pipeline/22_grids.py")
    cov = ctx.summary["sizes"][f"{s}km"]["coverage_default"]
    path = geom_path(ctx.province)
    colors = {1: CAT_BLUE, 2: CAT_ORANGE, 3: CAT_AQUA, 4: GREY_POP, 5: GREY_EMPTY, 6: SURFACE, 7: SURFACE}
    cmap = ListedColormap([colors[k] for k in range(1, 8)])
    norm = BoundaryNorm(np.arange(0.5, 8.5), cmap.N)

    H = 10.4
    fig = plt.figure(figsize=(FIG_W, H), dpi=DPI)
    header(fig, f"Aday hücreler J, 3 km ızgara — |J| = {tr(J)} (ızgaraya sığabilecek en fazla hastane)",
           f"Uygun hücre: il içi payı ≥ {pct_tr(r.min_inside, 0)} ve su + sulak alan < {pct_tr(r.max_water, 0)} "
           "(ESA WorldCover). Her hücrede en çok 1 hastane.\n"
           f"Aday: uygun ve (nüfus ≥ P_min = {tr(r.min_pop)} kişi/hücre  veya  8 komşudan biri ≥ {tr(near_thr)} kişi "
           f"= {tr(r.near_density)} kişi/km² × {tr(g.cell_km ** 2)} km²).\n"
           f"Kapsama denetimi (R = {tr(ctx.R_km)} km yol): adaysız kalan talep hücresi {tr(cov['uncovered_before_cells'])} "
           f"→ onarım {tr(cov['added_cells'])} hücre ekledi.")

    def draw(ax, lw):
        vals = cls.astype(float)
        draw_land(ax, ctx, path)
        draw_cells(ax, g, vals, cmap, norm, path, edge_lw=lw)
        sz = g.cell_km * 1000.0
        for k, hatch in ((6, "////"), (7, "...")):
            idx = np.flatnonzero(cls == k)
            pc = PatchCollection([Rectangle((g.ix[i] * sz, g.iy[i] * sz), sz, sz) for i in idx],
                                 facecolor=SURFACE, edgecolor="none", hatch=hatch, linewidth=0, zorder=3)
            pc.set_hatchcolor(INK2)
            ax.add_collection(pc)
            clip_to(ax, pc, path)
        idx = np.flatnonzero(cls == 3)
        if len(idx):   # onarım hücreleri ayrıca kalın mürekkep çerçeveyle (açık turkuaz için ikincil kodlama)
            pc = PatchCollection([Rectangle((g.ix[i] * sz, g.iy[i] * sz), sz, sz) for i in idx],
                                 facecolor="none", edgecolor=INK, linewidth=1.8, zorder=7)
            ax.add_collection(pc)

    ax = fig.add_axes((0.005, 0.045, 0.585, 0.80))
    base_axes(ax, province_extent(ctx))
    draw(ax, 0.3)
    draw_boundaries(ax, ctx, district_lw=0.8)
    draw_district_names(ax, ctx, skip=CENTRAL, avoid=ZOOM)
    zoom_frame(ax)
    draw_kizilay(ax, ctx, ms=15, label=False)
    xmin, ymin, xmax, ymax = ctx.province.bounds
    scale_bar(ax, xmin + 2_000, ymin + 6_000, 50)

    zx, zy = ZOOM[2] - ZOOM[0], ZOOM[3] - ZOOM[1]
    zw = 0.385
    zh = zw * FIG_W / H * zy / zx
    top = 0.845
    axz = fig.add_axes((0.605, top - zh, zw, zh))
    base_axes(axz, ZOOM)
    frame_axes(axz)
    draw(axz, 0.7)
    draw_boundaries(axz, ctx, district_lw=1.1, district_color=INK)
    draw_district_names(axz, ctx, box=ZOOM, min_km2=ZOOM_LABEL_MIN_KM2, fontsize=12.5)
    draw_kizilay(axz, ctx, ms=20)
    scale_bar(axz, ZOOM[0] + 2_500, ZOOM[1] + 3_000, 10, height=500)

    labels = dict(CAT_LABELS)
    labels[1] = f"aday — nüfus ≥ P_min ({tr(r.min_pop)} kişi)"
    labels[2] = f"aday — yerleşime yakın (komşu ≥ {tr(near_thr)} kişi)"
    labels[6] = f"uygun değil — su + sulak alan ≥ {pct_tr(r.max_water, 0)}"
    labels[7] = f"uygun değil — il içi payı < {pct_tr(r.min_inside, 0)}"
    handles = []
    for k in CAT_LABELS:
        lab = f"{labels[k]}: {tr(counts[k])}"
        if k in (6, 7):
            h = Patch(facecolor=SURFACE, edgecolor=INK2, hatch={6: "////", 7: "..."}[k], lw=0.6, label=lab)
        elif k == 3:
            h = Patch(facecolor=colors[k], edgecolor=INK, lw=1.8, label=lab)
        else:
            h = Patch(facecolor=colors[k], edgecolor=MUTED if k >= 4 else colors[k], lw=0.6, label=lab)
        handles.append(h)
    handles.append(Line2D([], [], marker="*", ls="", color=INK, mec=SURFACE, ms=15, label="Kızılay"))
    handles.append(ZOOM_HANDLE)
    leg = fig.legend(handles=handles, loc="upper left", bbox_to_anchor=(0.605, top - zh - 0.012), frameon=False,
                     fontsize=12.5, handlelength=1.6, handleheight=1.1, labelspacing=0.55,
                     title=f"Hücre sınıfı ({tr(g.n)} hücre)", title_fontproperties={"size": 13, "weight": "bold"})
    leg._legend_box.align = "left"
    footer(fig, SOURCE_LINE)
    stats = {"counts": {CAT_LABELS[k]: v for k, v in counts.items()}, "J": J}
    return save(fig, "faz2_adaylar_3km.png"), stats


# ---------------------------------------------------------------- 4) kaynak farkı, 3 km
def district_dissimilarity(ctx: Context, s: int) -> list[dict]:
    """İlçe içi uyuşmazlık: D = ½ Σ_parça |wp − ghs| / TÜİK2025 (parça = hücre ∩ ilçe)."""
    by = {}
    with open(GRID_DIR / f"grid_{s}km_pieces.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            k = row["ilce_key"]
            by[k] = by.get(k, 0) + abs(int(row["pop2025_wp"]) - int(row["pop2025_ghs"]))
    cal = {r["ilce_key"]: r for r in ctx.calib}
    out = []
    for r in ctx.tuik:
        k = r["ilce_key"]
        T = int(r["nufus_2025"])
        out.append({"ilce": r["ilce"], "ilce_key": k, "pop": T, "moved": by.get(k, 0) / 2, "D": by.get(k, 0) / 2 / T,
                    "factor_wp": float(cal[k]["wp_kalibrasyon_katsayisi"]),
                    "factor_ghs": float(cal[k]["ghs_kalibrasyon_katsayisi"])})
    return out


def fig_source_diff(ctx: Context) -> tuple[Path, dict]:
    s = 3
    g = ctx.grids[s]
    wp, ghs = g.pop("wp", 2025).astype(float), g.pop("ghs", 2025).astype(float)
    diff = wp - ghs
    vals = np.where((wp > 0) | (ghs > 0), diff, np.nan)
    P = float(wp.sum())
    D_cells = float(np.abs(diff).sum() / 2 / P)
    M = 10 ** math.ceil(math.log10(np.nanmax(np.abs(vals))))
    linthresh = 100.0
    norm = SymLogNorm(linthresh=linthresh, linscale=0.6, vmin=-M, vmax=M, base=10)
    cmap = diverging_cmap()
    path = geom_path(ctx.province)
    dd = sorted(district_dissimilarity(ctx, s), key=lambda r: -r["D"])
    moved_total = sum(r["moved"] for r in dd)
    D_pieces = moved_total / P

    # yerleşim inç cinsinden, yukarıdan aşağı
    ext = province_extent(ctx)
    y0 = 1.45
    map_w = 0.585 * FIG_W
    map_h = map_w * (ext[3] - ext[1]) / (ext[2] - ext[0])
    rx = 0.615 * FIG_W
    rw = FIG_W - rx - 0.15
    zoom_h = rw * (ZOOM[3] - ZOOM[1]) / (ZOOM[2] - ZOOM[0])
    bar_row = 0.215
    y_btitle = y0 + zoom_h + 0.3
    y_bars = y_btitle + 0.95
    H = y_bars + len(dd) * bar_row + 0.45 + 0.3
    fy = lambda inch: 1 - inch / H  # noqa: E731
    fig = plt.figure(figsize=(FIG_W, H), dpi=DPI)
    header(fig, "Kaynak farkı, 3 km ızgara — kalibre WorldPop − kalibre GHS-POP (kişi / hücre)",
           "İki dağılım da her ilçede TÜİK 2025 toplamına ölçeklendi: ilçe toplamları eşit, fark yalnız nüfusun ilçe İÇİNDE "
           "hangi hücreye konduğunda.\n"
           f"Mavi: WorldPop o hücreye daha çok kişi koyuyor · kırmızı: GHS-POP daha çok · gri: |fark| < {tr(linthresh)} kişi. "
           f"Nüfuslu hücre: WorldPop {tr(int((wp > 0).sum()))}, GHS-POP {tr(int((ghs > 0).sum()))}.")
    ax = fig.add_axes((0.005, fy(y0 + map_h), map_w / FIG_W, map_h / H))
    base_axes(ax, ext)
    draw_land(ax, ctx, path)
    qm = draw_cells(ax, g, vals, cmap, norm, path, edge_lw=0.25)
    draw_boundaries(ax, ctx, district_lw=0.8)
    draw_district_names(ax, ctx, skip=CENTRAL, avoid=ZOOM)
    zoom_frame(ax)
    draw_kizilay(ax, ctx, ms=15, label=False)
    xmin, ymin, xmax, ymax = ctx.province.bounds
    scale_bar(ax, xmin + 2_000, ymin + 6_000, 50)
    ax.legend(handles=[Patch(fc=LAND, ec=MUTED, lw=0.6, label="iki kaynakta da nüfussuz"),
                       Line2D([], [], marker="*", ls="", color=INK, mec=SURFACE, ms=15, label="Kızılay"), ZOOM_HANDLE],
              loc="upper left", frameon=False, fontsize=12.5, borderaxespad=0.2)

    # renk ölçeği ve açıklama (sol alt)
    y_cb = y0 + map_h + 0.55
    cax = fig.add_axes((0.04, fy(y_cb + 0.22), 0.5, 0.22 / H))
    cb = fig.colorbar(qm, cax=cax, orientation="horizontal")
    ticks = [t for e in range(int(math.log10(linthresh)), int(math.log10(M)) + 1) for t in (-(10.0 ** e), 10.0 ** e)]
    cb.locator = FixedLocator(sorted(ticks + [0.0]))
    cb.formatter = FuncFormatter(lambda v, _: tr_signed(v))
    cb.update_ticks()
    cb.ax.xaxis.set_minor_locator(NullLocator())
    cb.ax.tick_params(labelsize=11.5)
    cb.outline.set_edgecolor(MUTED)
    cb.set_label("WorldPop − GHS-POP, kişi / hücre (simetrik log ölçek)", fontsize=12.5, color=INK2)
    fig.text(0.04, fy(y_cb - 0.06), "← GHS-POP daha çok", ha="left", va="bottom", fontsize=12, color=INK2)
    fig.text(0.54, fy(y_cb - 0.06), "WorldPop daha çok →", ha="right", va="bottom", fontsize=12, color=INK2)

    # sol alt: kısa bulgular (hepsi yukarıdaki dizilerden)
    i_wp, i_ghs = int(np.nanargmax(vals)), int(np.nanargmin(vals))
    big = np.abs(diff) >= 1000
    in_zoom = (g.x >= ZOOM[0]) & (g.x < ZOOM[2]) & (g.y >= ZOOM[1]) & (g.y < ZOOM[3])
    cal_wp = [float(r["wp_kalibrasyon_katsayisi"]) for r in ctx.calib]
    cal_ghs = [float(r["ghs_kalibrasyon_katsayisi"]) for r in ctx.calib]
    facts = [
        f"• İlçe içinde farklı hücreye konan nüfus: {tr(moved_total)} kişi ({pct_tr(D_pieces)}).",
        f"• En büyük hücre farkı: WorldPop +{tr(vals[i_wp])} kişi ({district_name(ctx, g, i_wp)}), "
        f"GHS-POP +{tr(-vals[i_ghs])} kişi ({district_name(ctx, g, i_ghs)}).",
        f"• |fark| ≥ 1.000 kişi olan {tr(int(big.sum()))} hücrenin {tr(int((big & in_zoom).sum()))} tanesi merkez kutusunda.",
        f"• Kalibrasyon katsayısı (TÜİK ÷ kaynak): WorldPop {tr(min(cal_wp), 2)}–{tr(max(cal_wp), 2)}, "
        f"GHS-POP {tr(min(cal_ghs), 2)}–{tr(max(cal_ghs), 2)}.",
        "• Varsayılan dağılım WorldPop (KARARLAR #6); GHS-POP arayüzde seçilebilir alternatif.",
    ]
    fig.text(0.012, fy(y_cb + 1.0), "\n".join(facts), ha="left", va="top", fontsize=12.5, color=INK, linespacing=1.55)

    # sağ üst: merkez büyütmesi
    axz = fig.add_axes((rx / FIG_W, fy(y0 + zoom_h), rw / FIG_W, zoom_h / H))
    base_axes(axz, ZOOM)
    frame_axes(axz)
    draw_land(axz, ctx, path)
    draw_cells(axz, g, vals, cmap, norm, path, edge_lw=0.6)
    draw_boundaries(axz, ctx, district_lw=1.1, district_color=INK)
    draw_district_names(axz, ctx, box=ZOOM, min_km2=ZOOM_LABEL_MIN_KM2, fontsize=12.5)
    draw_kizilay(axz, ctx, ms=20)
    scale_bar(axz, ZOOM[0] + 2_500, ZOOM[1] + 3_000, 10, height=500)

    # sağ alt: ilçe içi uyuşmazlık payı
    fig.text(rx / FIG_W, fy(y_btitle), "İlçe içinde farklı hücreye konan nüfus payı (D)", ha="left", va="top",
             fontsize=15, fontweight="bold", color=INK)
    fig.text(rx / FIG_W, fy(y_btitle + 0.3),
             f"D = ½ Σ |WorldPop − GHS| / ilçe nüfusu. İl geneli: {pct_tr(D_pieces)} (≈ {tr(round(moved_total, -3))} kişi).\n"
             "Sağ sütun: GHS-POP'un ilçe kalibrasyon katsayısı (TÜİK ÷ GHS).",
             ha="left", va="top", fontsize=11.5, color=INK2, linespacing=1.4)
    bx0 = (rx + 1.45) / FIG_W
    bw = (rw - 1.45 - 1.05) / FIG_W
    n = len(dd)
    axb = fig.add_axes((bx0, fy(y_bars + n * bar_row), bw, n * bar_row / H))
    yy = np.arange(n)[::-1]
    Ds = np.array([r["D"] for r in dd])
    xmax_b = 10 * math.ceil(Ds.max() * 10 + 0.5)
    axb.barh(yy, Ds * 100, height=0.62, color=INK2, zorder=2)
    axb.set_yticks(yy)
    axb.set_yticklabels([r["ilce"] for r in dd], fontsize=12)
    axb.tick_params(axis="y", length=0)
    axb.set_xlim(0, xmax_b)
    axb.set_ylim(-0.6, n - 0.4)
    axb.xaxis.set_major_locator(FixedLocator(list(range(0, xmax_b + 1, 10))))
    axb.xaxis.set_major_formatter(FuncFormatter(lambda v, _: "%" + tr(v)))
    axb.tick_params(axis="x", labelsize=11, colors=MUTED)
    axb.grid(axis="x", color=HAIRLINE, lw=0.8, zorder=0)
    for sp in ("top", "right", "left"):
        axb.spines[sp].set_visible(False)
    axb.spines["bottom"].set_color(MUTED)
    fx = (rx + rw - 0.05) / FIG_W
    for y, r in zip(yy, dd, strict=True):
        axb.text(r["D"] * 100 + xmax_b * 0.015, y, pct_tr(r["D"]), va="center", ha="left", fontsize=11.5, color=INK)
        fig.text(fx, fy(y_bars + (n - 1 - y + 0.5) * bar_row), tr(r["factor_ghs"], 2), va="center", ha="right",
                 fontsize=11.5, color=INK2)
    fig.text(fx, fy(y_bars - 0.05), "GHS kat.", va="bottom", ha="right", fontsize=11, color=INK2)
    footer(fig, SOURCE_LINE)
    stats = {"D_province_pieces": D_pieces, "D_province_cells": D_cells, "moved_total_pieces": moved_total,
             "max_wp_more": [float(vals[i_wp]), district_name(ctx, g, i_wp)],
             "max_ghs_more": [float(-vals[i_ghs]), district_name(ctx, g, i_ghs)],
             "n_abs_diff_ge_1000": int(big.sum()), "n_abs_diff_ge_1000_in_zoom": int((big & in_zoom).sum()),
             "max_abs_diff": float(np.nanmax(np.abs(vals))),
             "D_by_district": [(r["ilce"], round(r["D"], 4)) for r in dd],
             "n_pop_cells_wp": int((wp > 0).sum()), "n_pop_cells_ghs": int((ghs > 0).sum())}
    return save(fig, "faz2_kaynak_farki_3km.png"), stats


# ---------------------------------------------------------------- ana akış
def main() -> None:
    ctx = load_context()
    print("Bağlam yüklendi; grid_summary.json ile hücre / talep / |J| / onarım sayıları birebir.")
    for fn in (fig_density, fig_grids, fig_candidates, fig_source_diff):
        path, stats = fn(ctx)
        print(f"  {path.relative_to(common.ROOT)}  ({path.stat().st_size / 1e3:,.0f} kB)")
        print("    " + json.dumps(stats, ensure_ascii=False))


if __name__ == "__main__":
    main()
