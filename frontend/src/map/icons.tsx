/**
 * Harita araç çubuğu ve lejant için küçük satır içi SVG simgeleri (simge kütüphanesi yok; hepsi yerel).
 * `currentColor` ile çizilir: tema ve düğme rengini izler.
 */
import type { SVGProps } from 'react';

type P = SVGProps<SVGSVGElement> & { size?: number };

function Svg({ size = 18, children, ...rest }: P & { children: React.ReactNode }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden {...rest}>
      {children}
    </svg>
  );
}

export const IconLayers = (p: P) => (
  <Svg {...p}>
    <path d="M12 3 2 8l10 5 10-5-10-5Z" />
    <path d="m2 13 10 5 10-5" />
  </Svg>
);
export const IconPointer = (p: P) => (
  <Svg {...p}>
    <path d="m5 3 14 7-6 2-2 6L5 3Z" />
  </Svg>
);
export const IconPlusCell = (p: P) => (
  <Svg {...p}>
    <rect x="3.5" y="3.5" width="17" height="17" rx="2" />
    <path d="M12 8v8M8 12h8" />
  </Svg>
);
export const IconXCell = (p: P) => (
  <Svg {...p}>
    <rect x="3.5" y="3.5" width="17" height="17" rx="2" />
    <path d="m9 9 6 6M15 9l-6 6" />
  </Svg>
);
export const IconTilt = (p: P) => (
  <Svg {...p}>
    <path d="M3 17 12 21l9-4-9-4-9 4Z" />
    <path d="M7 15V7M12 13V3M17 15V9" />
  </Svg>
);
export const IconFit = (p: P) => (
  <Svg {...p}>
    <path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" />
  </Svg>
);
export const IconDownload = (p: P) => (
  <Svg {...p}>
    <path d="M12 4v11M7 10l5 5 5-5M5 20h14" />
  </Svg>
);
export const IconLegend = (p: P) => (
  <Svg {...p}>
    <circle cx="6" cy="7" r="2" />
    <circle cx="6" cy="17" r="2" />
    <path d="M11 7h9M11 17h9" />
  </Svg>
);
export const IconChevron = ({ open, ...p }: P & { open?: boolean }) => (
  <Svg {...p} style={{ transform: open ? 'rotate(180deg)' : undefined, transition: 'transform 120ms' }}>
    <path d="m6 9 6 6 6-6" />
  </Svg>
);
