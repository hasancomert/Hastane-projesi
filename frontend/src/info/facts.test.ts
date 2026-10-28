import { describe, expect, it } from 'vitest';

import type { PSweep, SolveResultOk } from '../api/types.ts';
import { plateauFacts, quoteWith, rangesText, ruralFacts, sentenceWith, sizePiles } from './facts.ts';

describe('rangesText', () => {
  it('ardışık sayıları aralık yazar', () => {
    expect(rangesText([82, 83, 84, 85, 86, 87, 88, 89, 90, 91, 92, 93, 94, 95, 96])).toBe('82–96');
    expect(rangesText([86, 83, 84, 90])).toBe('83–84, 86, 90');
    expect(rangesText([69])).toBe('69');
    expect(rangesText([])).toBe('');
  });
});

describe('metin çıkarımı', () => {
  const notes =
    'Nüfus kapsamı: "ADNKS’de ülke sınırları içinde ikamet eden vatandaşlar kapsanmaktadır." ve "Kurs, turizm, bilimsel araştırma vb. nedenlerle 3 aydan kısa süreli vize sahibi yabancılar ile geçici koruma statüsüyle bulunanlar nüfusa dâhil değildir." (dipnot).';
  it('tırnak içindeki alıntıyı bütün alır (içindeki "vb." kısaltmasına takılmaz)', () => {
    expect(quoteWith(notes, 'geçici koruma')).toBe(
      'Kurs, turizm, bilimsel araştırma vb. nedenlerle 3 aydan kısa süreli vize sahibi yabancılar ile geçici koruma statüsüyle bulunanlar nüfusa dâhil değildir.',
    );
    expect(quoteWith(notes, 'yok böyle')).toBeNull();
  });
  it('cümleyi ". " sınırlarıyla alır', () => {
    const t = 'Birinci cümle. Not: WorldPop R2025A yayıncısınca "alfa" sürüm olarak tanımlanır. Son.';
    expect(sentenceWith(t, 'alfa')).toBe('Not: WorldPop R2025A yayıncısınca "alfa" sürüm olarak tanımlanır.');
    expect(sentenceWith(null, 'x')).toBeNull();
  });
});

function hospital(cls: string, beds: number, atBound: 'ust' | 'alt' | null, occ: number, district: string) {
  return {
    class: cls,
    beds,
    occupancy: occ,
    district,
    population: 1000,
    size: { class_min_beds: cls === 'IV-C' ? 50 : 200, class_max_beds: cls === 'IV-C' ? 199 : 399, at_bound: atBound, next_class: atBound === 'ust' ? { class: cls === 'IV-C' ? 'V-B' : 'V-C' } : undefined },
  };
}

describe('çözümden türetilen sayılar', () => {
  const r = {
    hospitals: [
      hospital('IV-C', 199, 'ust', 1, 'Keçiören'),
      hospital('IV-C', 199, 'ust', 1, 'Sincan'),
      hospital('IV-C', 50, 'alt', 0.156, 'Nallıhan'),
      hospital('IV-C', 50, 'alt', 0.52, 'Şereflikoçhisar'),
      hospital('V-B', 399, 'ust', 1, 'Altındağ'),
      hospital('IV-C', 110, null, 1, 'Çankaya'),
    ],
    layers: { rings: { R_km: 30 } },
  } as unknown as SolveResultOk;
  it('sınıf eşiklerinde yığılmayı sayar', () => {
    const p = sizePiles(r);
    expect(p.upper.map((x) => [x.code, x.beds, x.n])).toEqual([
      ['IV-C', 199, 2],
      ['V-B', 399, 1],
    ]);
    expect(p.lower.map((x) => [x.code, x.beds, x.n])).toEqual([['IV-C', 50, 2]]);
  });
  it('kırsal hastaneler: en küçük boyuttakiler doluluğa göre artan', () => {
    const f = ruralFacts(r, null);
    expect(f.atMin.map((h) => h.district)).toEqual(['Nallıhan', 'Şereflikoçhisar']);
    expect(f.lowest?.district).toBe('Nallıhan');
    expect(f.R).toBe(30);
  });
});

describe('plateauFacts', () => {
  it('düz dip ve komşular', () => {
    const sweep = {
      grid_km: 3,
      free: { ub_tl: 381.3e9, lb_tl: 381.08e9, gap: 0.00062, n_hospitals: 89 },
      points: [
        { p: 88, ub_tl: 381.318e9, lb_tl: 381.078e9, gap: 0.00063, fark_tl: 3.9e6, fark_gap_icinde: true },
        { p: 90, ub_tl: 381.34e9, lb_tl: 381.078e9, gap: 0.00069, fark_tl: 25.9e6, fark_gap_icinde: true },
      ],
      acceptance: { best_p: 89, free_n: 89, plateau_p: [90, 88, 89], free_n_unique_within_tol: false, summary_tr: '', passed: true },
    } as unknown as PSweep;
    const f = plateauFacts(sweep)!;
    expect(f.plateau).toEqual([88, 89, 90]);
    expect(f.unique).toBe(false);
    expect(f.neighbors.map((n) => [n.p, n.diffTl])).toEqual([
      [88, 3.9e6],
      [90, 25.9e6],
    ]);
  });
});
