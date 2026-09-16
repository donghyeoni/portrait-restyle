"""직업·12지신 참고 이미지의 누끼 마스크를 한 번에 만든다 (steps/refmask.py).

  python scripts/precompute_masks.py                 # inswapper 컬렉션 전부, 있는 것은 건너뜀
  python scripts/precompute_masks.py --collection jobs --gender female --force
  python scripts/precompute_masks.py --preview logs/mask_preview   # 검수용 체커보드 합성 PNG 도 저장

런타임(워커·GPU 서비스)은 마스크가 있으면 쓰고 없으면 누끼 모델로 떨어지므로, 이 스크립트는 언제 돌려도 안전하다.
GPU 를 쓰지 않으려면 CUDA_VISIBLE_DEVICES= 로 실행한다 (BiRefNet CPU 장당 약 15초).
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}


def checkerboard_preview(final_bgr, mask, cell=32):
    import numpy as np
    h, w = mask.shape
    yy, xx = np.mgrid[0:h, 0:w]
    board = np.where(((yy // cell) + (xx // cell)) % 2 == 0, 200, 120).astype(np.uint8)
    board = np.dstack([board] * 3)
    a = (mask.astype(np.float32) / 255.0)[..., None]
    return (final_bgr * a + board * (1 - a)).astype(np.uint8)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--collection", action="append", help="기본: engine=inswapper 인 컬렉션 전부")
    ap.add_argument("--gender", choices=["female", "male"], help="기본: 둘 다")
    ap.add_argument("--force", action="store_true", help="이미 있는 마스크도 다시 만든다")
    ap.add_argument("--preview", help="검수용 합성 PNG 를 저장할 폴더 (data/output 이 아닌 곳)")
    a = ap.parse_args()

    import cv2
    import manifests
    from engines.inswapper import crop_upper
    from steps import refmask
    from steps.crop import OVERRIDE
    from steps.cutout import cutout
    from steps.faces import biggest, face_app

    cols = [manifests.load(c) for c in a.collection] if a.collection else \
           [c for c in manifests.all_collections().values() if c.engine == "inswapper"]
    fa = face_app("buffalo_l", ctx_id=-1)
    prev_dir = pathlib.Path(a.preview) if a.preview else None
    if prev_dir:
        prev_dir.mkdir(parents=True, exist_ok=True)

    made = skipped = failed = 0
    for coll in cols:
        for gdir in sorted(p for p in coll.reference_dir().iterdir() if p.is_dir()):
            if a.gender and gdir.name != a.gender:
                continue
            for ref in sorted(p for p in gdir.iterdir() if p.suffix.lower() in IMG_EXT and ".mask" not in p.name):
                above = OVERRIDE.get(ref.stem, {}).get("above", 1.0)
                if not a.force and refmask.load(ref, (1152, 896), above) is not None:
                    skipped += 1
                    continue
                t0 = time.time()
                try:
                    mask, meta = refmask.build(ref, fa, above, cutout)
                except Exception as e:                # noqa: BLE001
                    failed += 1
                    print(f"실패 {coll.id}/{gdir.name}/{ref.name}: {e}", flush=True)
                    continue
                png = refmask.save(ref, mask, meta)
                made += 1
                cover = float((mask > 127).mean()) * 100
                print(f"{coll.id}/{gdir.name}/{ref.name} -> {png.name}  인물 {cover:.0f}%  {time.time() - t0:.1f}s", flush=True)
                if prev_dir:
                    tgt = cv2.imread(str(ref))
                    face = biggest(fa.get(tgt))
                    final, _, _ = crop_upper(tgt, face.bbox, above)
                    cv2.imwrite(str(prev_dir / f"{coll.id}_{gdir.name}_{ref.stem}.png"), checkerboard_preview(final, mask))
    print(f"완료 {made} / 건너뜀 {skipped} / 실패 {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
