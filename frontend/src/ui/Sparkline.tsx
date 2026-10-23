/**
 * Küçük çizgi grafik (SVG; ECharts gerektirmez — ilerleme kutusunda ilk açılışı ağırlaştırmasın): tek seri, 2 px
 * çizgi, son noktada 8 px işaret (2 px yüzey halkalı), isteğe bağlı yatay referans çizgisi (ince, düz).
 * Tek seri olduğu için lejant yok; ne çizildiğini çağıranın başlığı söyler. Gerçek piksel genişliğiyle çizilir
 * (işaret elipse dönüşmesin).
 */
import { useElementSize } from '@mantine/hooks';

export interface SparklineProps {
  /** [x, y] noktaları (x artan) */
  points: [number, number][];
  height?: number;
  /** yatay referans (ör. hedef fark) */
  reference?: number | null;
  color?: string;
  ariaLabel: string;
  /** y'yi log10 ölçekte çiz (fark gibi birkaç büyüklük mertebesine yayılan değerler) */
  log?: boolean;
}

export function Sparkline({ points, height = 40, reference, color = 'var(--mantine-primary-color-filled)', ariaLabel, log }: SparklineProps) {
  const { ref, width } = useElementSize<HTMLDivElement>();
  const W = Math.max(40, Math.round(width));
  const H = height;
  const pad = 6;
  const tf = (y: number) => (log ? Math.log10(Math.max(y, 1e-6)) : y);
  const pts = points.filter(([x, y]) => Number.isFinite(x) && Number.isFinite(y));
  let body = null;
  if (pts.length > 0 && width > 0) {
    const xs = pts.map((p) => p[0]);
    const ys = pts.map((p) => tf(p[1]));
    const refY = reference != null && Number.isFinite(reference) ? tf(reference) : null;
    let y0 = Math.min(...ys, ...(refY != null ? [refY] : []));
    let y1 = Math.max(...ys, ...(refY != null ? [refY] : []));
    if (y1 - y0 < 1e-12) {
      y0 -= 1;
      y1 += 1;
    }
    const x0 = Math.min(...xs);
    const x1 = Math.max(...xs, x0 + 1e-9);
    const sx = (x: number) => pad + ((x - x0) / (x1 - x0)) * (W - 2 * pad);
    const sy = (y: number) => H - pad - ((y - y0) / (y1 - y0)) * (H - 2 * pad);
    const d = pts.map((p, i) => `${i ? 'L' : 'M'}${sx(p[0]).toFixed(1)},${sy(tf(p[1])).toFixed(1)}`).join(' ');
    const last = pts[pts.length - 1];
    body = (
      <>
        {refY != null && (
          <line x1={pad} x2={W - pad} y1={sy(refY)} y2={sy(refY)} stroke="var(--mantine-color-dimmed)" strokeOpacity={0.6} strokeWidth={1} />
        )}
        <path d={d} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        <circle cx={sx(last[0])} cy={sy(tf(last[1]))} r={4} fill={color} stroke="var(--mantine-color-body)" strokeWidth={2} />
      </>
    );
  }
  return (
    <div ref={ref} style={{ width: '100%', height: H }}>
      <svg role="img" aria-label={ariaLabel} width={W} height={H} viewBox={`0 0 ${W} ${H}`} style={{ display: 'block', maxWidth: '100%' }}>
        {body}
      </svg>
    </div>
  );
}
