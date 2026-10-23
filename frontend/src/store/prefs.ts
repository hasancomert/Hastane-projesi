/**
 * Tarayıcıda kalan kişisel tercihler (tema, katman görünürlüğü). localStorage özel pencerede, temizlenmiş site
 * verisinde ya da önizlemede kullanılamayabilir: her erişim try/catch içinde, yoksa varsayılanlar geçerli.
 * Paylaşılması ya da kalıcı olması gereken durum burada TUTULMAZ.
 */
const PREFIX = 'hastane.';

export function readPref<T = string>(key: string): T | null {
  try {
    const raw = window.localStorage.getItem(PREFIX + key);
    return raw === null ? null : (JSON.parse(raw) as T);
  } catch {
    return null;
  }
}

export function writePref(key: string, value: unknown): void {
  try {
    window.localStorage.setItem(PREFIX + key, JSON.stringify(value));
  } catch {
    /* depolama kapalı: tercih yalnız bu oturumda geçerli */
  }
}
