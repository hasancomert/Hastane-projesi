/**
 * KaTeX ile formül (yalnız tembel yüklenen Model sekmesinden içe aktarılır; KaTeX ve yazı tipleri derlemeye gömülü,
 * internet gerekmez). `strict: 'ignore'`: Türkçe harfler \text{} içinde uyarı üretmesin.
 */
import katex from 'katex';
import 'katex/dist/katex.min.css';
import { useMemo } from 'react';

export function Tex({ tex, block, label }: { tex: string; block?: boolean; label?: string }) {
  const html = useMemo(
    () => katex.renderToString(tex, { displayMode: !!block, throwOnError: false, strict: 'ignore', output: 'htmlAndMathml', trust: false }),
    [tex, block],
  );
  if (block) return <div className="model-eq" aria-label={label} dangerouslySetInnerHTML={{ __html: html }} />;
  return <span aria-label={label} dangerouslySetInnerHTML={{ __html: html }} />;
}
