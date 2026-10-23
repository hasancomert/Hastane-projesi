import { describe, expect, it } from 'vitest';

import type { SolveResultOk } from '../api/types.ts';
import { optimalityLabel, summarySentence } from './optimality.ts';

function res(p: { status: string; gap: number | null; lb?: number | null; msg?: string; mode?: string }): SolveResultOk {
  return {
    status: p.status,
    status_tr: p.status,
    mode: p.mode ?? 'toplam',
    message_tr: p.msg ?? 'En iyi bulunan çözüm: 89 hastane (67 küçük, 22 orta, 0 büyük, 0 şehir hastanesi), toplam 381,3 milyar TL.',
    bounds: { ub_tl: 381.3e9, lb_tl: p.lb === undefined ? 381.08e9 : p.lb, gap: p.gap },
  } as unknown as SolveResultOk;
}

describe('optimalityLabel — başlık, özet ve rozetler tek kural', () => {
  it('süre dolmuş ama kanıtlı fark hedefin içinde → optimum', () => {
    const o = optimalityLabel(res({ status: 'sure_doldu', gap: 0.00062 }), 0.01);
    expect(o.kind).toBe('optimum');
    expect(o.head).toBe('Optimum (kanıtlı fark %0,06)');
    expect(o.badge).toContain('kanıtlı fark %0,06');
    expect(o.tooltip).toContain('sure_doldu');
  });
  it('motor "optimal" dese de fark hedefin üstündeyse optimum denmez', () => {
    const o = optimalityLabel(res({ status: 'optimal', gap: 0.0133 }), 0.01);
    expect(o.kind).toBe('best');
    expect(o.head).toBe('En iyi bulunan çözüm (fark %1,33)');
  });
  it('daha sıkı kanıtlanmış çözüm daha zayıf etiket almaz', () => {
    const better = optimalityLabel(res({ status: 'sure_doldu', gap: 0.0006 }), 0.01);
    const worse = optimalityLabel(res({ status: 'optimal', gap: 0.0031 }), 0.01);
    expect(better.kind).toBe('optimum');
    expect(worse.kind).toBe('optimum');
  });
  it('durdurulan iş hiçbir zaman optimum değildir', () => {
    const o = optimalityLabel(res({ status: 'durduruldu', gap: 0.002 }), 0.01, true);
    expect(o.kind).toBe('stopped');
    expect(o.head).toContain('Durduruldu');
  });
  it('alt sınır yoksa sezgisel', () => {
    const o = optimalityLabel(res({ status: 'sezgisel', gap: null, lb: null }), 0.01);
    expect(o.kind).toBe('heuristic');
  });
  it('özet cümlenin başı tutarlı etiketle değişir', () => {
    const r = res({ status: 'sure_doldu', gap: 0.00062 });
    expect(summarySentence(r, optimalityLabel(r, 0.01))).toBe(
      'Optimum (kanıtlı fark %0,06): 89 hastane (67 küçük, 22 orta, 0 büyük, 0 şehir hastanesi), toplam 381,3 milyar TL.',
    );
    const r2 = res({ status: 'optimal', gap: 0.02, msg: 'Optimum: 70 hastane.' });
    expect(summarySentence(r2, optimalityLabel(r2, 0.01))).toBe('En iyi bulunan çözüm (fark %2,00): 70 hastane.');
  });
});
