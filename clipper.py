"""Auto Clipper — ligne de commande.

  python clipper.py <url_ou_fichier> --clips 5 --layout auto --min 20 --max 60 --lang fr
  python clipper.py doctor
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "doctor":
        from clipper.doctor import print_report

        return print_report()

    from clipper.config import get_config

    d = get_config()["defaults"]
    ap = argparse.ArgumentParser(prog="clipper.py", description="Génère des clips verticaux sous-titrés à partir d'une vidéo.")
    ap.add_argument("source", help="Lien YouTube ou chemin d'un fichier vidéo (MP4, MOV, MKV, WEBM)")
    ap.add_argument("--clips", type=int, default=d["clips"], help="nombre de clips (défaut %(default)s)")
    ap.add_argument("--layout", choices=["auto", "facecam", "blur", "track"], default=d["layout"])
    ap.add_argument("--min", dest="min_duration", type=float, default=d["min_duration"], help="durée minimale (s)")
    ap.add_argument("--max", dest="max_duration", type=float, default=d["max_duration"], help="durée maximale (s)")
    ap.add_argument("--lang", choices=["auto", "fr", "en"], default=d["language"])
    ap.add_argument("--preset", default=d["subtitle_preset"], help="preset de sous-titres")
    ap.add_argument("--no-hook", action="store_true", help="ne pas afficher le titre hook")
    ap.add_argument("--start", type=float, default=None, help="début de la plage à analyser (s)")
    ap.add_argument("--end", type=float, default=None, help="fin de la plage à analyser (s)")
    args = ap.parse_args()

    from backend import db, tasks
    from clipper.download import VIDEO_EXTS, is_youtube_url
    from clipper.utils import STEP_LABELS, Cancelled, ClipperError, Context, setup_logging

    setup_logging()
    opts = {
        "clips": args.clips, "min_duration": args.min_duration, "max_duration": args.max_duration,
        "layout": args.layout, "language": args.lang, "subtitle_preset": args.preset,
        "hook_title": not args.no_hook, "range_start": args.start, "range_end": args.end,
    }
    if is_youtube_url(args.source):
        p = db.create_project(source_type="youtube", source_url=args.source.strip(), source_name=args.source.strip(), options=opts)
    else:
        f = Path(args.source).expanduser()
        if not f.exists():
            print(f"Fichier introuvable : {f}")
            return 2
        if f.suffix.lower() not in VIDEO_EXTS:
            print(f"Format non supporté : {f.suffix}")
            return 2
        opts.update({"upload_path": str(f.resolve()), "move_upload": False})
        p = db.create_project(source_type="file", source_name=f.name, title=f.stem, options=opts)

    pid = p["id"]
    print(f"Projet {pid}")
    state = {"step": "", "t": time.time()}

    def on_progress(step, pct, msg):
        overall = tasks.overall_progress("process", step, pct)
        label = STEP_LABELS.get(step, step)
        sys.stdout.write(f"\r  [{overall:5.1f} %] {label} — {msg[:60]:<60}")
        sys.stdout.flush()
        if step != state["step"]:
            state["step"] = step

    def on_log(msg, level):
        sys.stdout.write("\r" + " " * 100 + "\r")

    ctx = Context(pid, on_progress=on_progress, on_log=on_log)
    t0 = time.time()
    try:
        tasks.process_project(ctx, pid)
    except Cancelled:
        db.update_project(pid, status="cancelled")
        print("\nAnnulé.")
        return 1
    except ClipperError as e:
        db.update_project(pid, status="error", error=str(e))
        print(f"\nErreur : {e}")
        return 1
    except KeyboardInterrupt:
        db.update_project(pid, status="interrupted")
        print("\nInterrompu.")
        return 1
    print(f"\n\nTerminé en {time.time() - t0:.0f} s. Clips dans : {ctx.outdir}")
    for c in db.list_clips(pid):
        print(f"  • [{c['score']:3d}] {c.get('file')}  —  {c.get('hook') or ''}")
        if c.get("hashtags"):
            print(f"         {c.get('title') or ''}  {' '.join(c['hashtags'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
