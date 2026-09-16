"""Cut out every image under data/output with rembg + BiRefNet-portrait (GPU)."""
import glob, os, sys, time
import torch  # loads CUDA/cuDNN libs before onnxruntime
from PIL import Image
from rembg import new_session, remove

SRC = "data/output"
DST = "data/output_cutout"
os.makedirs(DST, exist_ok=True)
log = open(os.path.join(DST, "_progress.log"), "a", buffering=1)

files = sorted(p for p in glob.glob(os.path.join(SRC, "**", "*"), recursive=True)
               if p.lower().endswith((".png", ".jpg", ".jpeg", ".webp")))
todo = [p for p in files if not os.path.exists(os.path.join(DST, os.path.splitext(os.path.relpath(p, SRC))[0] + ".png"))]
SHARD, NSHARD = (int(sys.argv[1]), int(sys.argv[2])) if len(sys.argv) > 2 else (0, 1)
todo = todo[SHARD::NSHARD]
print(f"shard {SHARD}/{NSHARD}: total {len(files)} images, {len(todo)} to do", file=log)

sess = new_session("birefnet-portrait", providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
print("providers", sess.inner_session.get_providers(), file=log)

t0 = time.time()
for i, p in enumerate(todo, 1):
    rel = os.path.splitext(os.path.relpath(p, SRC))[0] + ".png"
    out = os.path.join(DST, rel)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    try:
        im = Image.open(p).convert("RGB")
        cut = remove(im, session=sess, post_process_mask=True)
        cut.save(out)
    except Exception as e:
        print(f"ERROR {p}: {e}", file=log)
    if i % 20 == 0 or i == len(todo):
        el = time.time() - t0
        print(f"{i}/{len(todo)}  {el/60:.1f} min elapsed, {el/i:.2f} s/img", file=log)

sheets = os.path.join(DST, "_sheets"); os.makedirs(sheets, exist_ok=True)
for theme in sorted(d for d in os.listdir(DST) if os.path.isdir(os.path.join(DST, d)) and not d.startswith("_")):
    cuts = sorted(glob.glob(os.path.join(DST, theme, "**", "*.png"), recursive=True))
    if not cuts:
        continue
    tiles = []
    for c in cuts:
        im = Image.open(c).convert("RGBA"); im.thumbnail((180, 240))
        bg = Image.new("RGBA", (180, 240), (40, 44, 52, 255))
        bg.alpha_composite(im, ((180 - im.width) // 2, 240 - im.height))
        tiles.append(bg.convert("RGB"))
    cols = 14; rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * 184, rows * 244), (20, 20, 20))
    for k, t in enumerate(tiles):
        sheet.paste(t, ((k % cols) * 184, (k // cols) * 244))
    sheet.save(os.path.join(sheets, f"{theme}.jpg"), quality=80)
    print(f"sheet {theme}: {len(tiles)} tiles", file=log)
print("__ALL_DONE__", file=log)

