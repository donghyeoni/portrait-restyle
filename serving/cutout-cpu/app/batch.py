"""Batch CLI: python -m app.batch <input_dir> <output_dir>

Mirrors the input folder structure, writes <name>.png with transparency,
skips files whose output already exists.
"""
import glob
import os
import sys
import time

from PIL import Image

from .engine import cutout

EXTS = (".png", ".jpg", ".jpeg", ".webp")


def main(src: str, dst: str) -> int:
    files = sorted(p for p in glob.glob(os.path.join(src, "**", "*"), recursive=True) if p.lower().endswith(EXTS))
    todo = []
    for p in files:
        rel = os.path.splitext(os.path.relpath(p, src))[0] + ".png"
        out = os.path.join(dst, rel)
        if not os.path.exists(out):
            todo.append((p, out))
    print(f"{len(files)} images, {len(todo)} to do", flush=True)
    t0 = time.time()
    errors = 0
    for i, (p, out) in enumerate(todo, 1):
        os.makedirs(os.path.dirname(out), exist_ok=True)
        try:
            cutout(Image.open(p)).save(out)
        except Exception as e:
            errors += 1
            print(f"ERROR {p}: {e}", flush=True)
        if i % 10 == 0 or i == len(todo):
            el = time.time() - t0
            print(f"{i}/{len(todo)}  {el/60:.1f} min, {el/i:.1f} s/img", flush=True)
    print(f"done, {errors} errors", flush=True)
    return 1 if errors else 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2]))
