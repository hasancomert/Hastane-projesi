"""Sunucuyu başlatır: python -m backend.run [--port 8000] [--no-browser]

Yalnız 127.0.0.1'e bağlanır (dışarıdan erişilemez), tek uvicorn işçisi (iş kuyruğu ve çözücü süreci tek olmalı).
Windows uyumu: çözücü işçisi 'spawn' ile başlar ve bu modülü '__mp_main__' olarak yeniden içe aktarır; bu yüzden
modül düzeyinde ağır içe aktarma ya da yan etki yoktur ve çalıştırma 'if __name__ == "__main__"' korumalıdır.
Faz 8'deki start.bat bu komutu çağırır.
"""
from __future__ import annotations

import argparse
import sys

HOST = "127.0.0.1"


def _parse(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="python -m backend.run", description="Ankara hastane yerleşimi sunucusu")
    ap.add_argument("--port", type=int, default=8000, help="dinlenecek port (varsayılan 8000)")
    ap.add_argument("--no-browser", action="store_true", help="tarayıcıyı açma")
    ap.add_argument("--log-level", default="info", choices=["critical", "error", "warning", "info", "debug"])
    return ap.parse_args(argv)


def _console_utf8() -> None:
    """Çıktı bir dosyaya/boruya yönlendirilmişse ve kodlama UTF-8 değilse (Windows'ta ör. cp1252) Türkçe karakterler
    yazdırma hatasına yol açmasın (Faz 4 incelemesi bulgu 10; start.bat ayrıca PYTHONUTF8=1 ayarlamalı)."""
    for st in (sys.stdout, sys.stderr):
        try:
            if st is not None and not st.isatty() and (st.encoding or "").lower().replace("-", "") != "utf8":
                st.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def main(argv: list[str] | None = None) -> int:
    _console_utf8()
    args = _parse(argv)
    import logging
    import multiprocessing as mp
    import threading
    import webbrowser

    import uvicorn

    from backend.app.main import create_app

    try:
        mp.set_start_method("spawn", force=False)        # Linux'ta da Windows ile aynı davranış
    except RuntimeError:
        pass
    logging.basicConfig(level=getattr(logging, args.log_level.upper()), format="%(asctime)s %(name)s: %(message)s")
    url = f"http://{HOST}:{args.port}/"
    config = uvicorn.Config(create_app(), host=HOST, port=args.port, workers=1, loop="asyncio", http="h11",
                            log_level=args.log_level, timeout_graceful_shutdown=5, access_log=False)
    server = uvicorn.Server(config)

    if not args.no_browser:
        def open_when_ready() -> None:
            import time
            for _ in range(600):                          # en çok ≈60 sn bekle
                if server.started:
                    webbrowser.open(url)
                    return
                if server.should_exit:
                    return
                time.sleep(0.1)
        threading.Thread(target=open_when_ready, name="tarayici", daemon=True).start()
    print(f"Sunucu: {url}  (durdurmak için Ctrl+C)", flush=True)
    try:
        server.run()
    except KeyboardInterrupt:          # uvicorn kapanıştan sonra Ctrl+C sinyalini yeniden yükseltir (Windows)
        pass
    print("Sunucu kapandı.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
