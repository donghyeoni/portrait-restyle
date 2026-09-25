"""InsightFace inswapper 기반 얼굴 교체.

확산 모델을 쓰지 않는다. 대상 이미지의 얼굴 랜드마크에 맞춰
원본 얼굴의 ID 임베딩으로 얼굴만 갈아끼우고 되붙인다.
의상·포즈·손·배경은 원본 픽셀 그대로 남는다.

  python -m generators.zodiac.swap --source test1 --out outputs/zodiac/test1

앞머리 처리: 원본 인물의 앞머리는 inswapper 출력에 흐릿한 잔상으로만 남는다.
지우거나(--bangs drop) 그대로 두는(keep) 두 가지만 지원한다.
원본 앞머리를 워프해 얹는 방식을 만들어 봤지만 실패했다 — 머리카락만이 아니라
원본의 헤어라인 형태와 배경까지 따라 들어와 후드 위로 번지는 얼룩이 됐다.
제대로 하려면 원본에서 머리카락만 분리(세그멘테이션+매팅)하고 조명을 다시
입혀야 한다. 별개의 파이프라인이다.

레퍼런스 모델과 대상 인물의 피부톤·헤어가 다르면 그 차이는 남는다.
후처리 색보정을 두 번 시도해 두 번 다 실패했다 (docs/DECISIONS.md 참조).
해법은 보정이 아니라 레퍼런스 교체다.
"""
from __future__ import annotations

import argparse, json, pathlib, sys, time

import cv2
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
MODELS = ROOT / "models"

from steps.faces import biggest, face_app   # noqa: E402
from steps.glasses import (THRESH, glasses_mask,  # noqa: E402
                            glasses_ratio_ms as glasses_ratio, paste_glasses)

SRC_DIR = ROOT / "data" / "faces" / "normalized"
DATA = ROOT / "data"

_swapper = None
_restorer = None


def swapper():
    global _swapper
    if _swapper is None:
        import insightface
        _swapper = insightface.model_zoo.get_model(
            str(MODELS / "insightface" / "inswapper_128.onnx"),
            providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
    return _swapper


def restorer():
    """GFPGAN. inswapper 출력이 128px 이라 큰 얼굴에서는 흐리다."""
    global _restorer
    if _restorer is None:
        # basicsr 1.4 는 torchvision 0.17 에서 없어진 functional_tensor 를 import 한다. 같은 함수가 functional 에 있다
        import torchvision.transforms.functional as _tf
        sys.modules.setdefault("torchvision.transforms.functional_tensor", _tf)
        from gfpgan import GFPGANer
        _restorer = GFPGANer(model_path=str(MODELS / "face_restore" / "GFPGANv1.4.pth"),
                             upscale=1, arch="clean", channel_multiplier=2)
    return _restorer


def restore_face(img, strength: float):
    """GFPGAN 결과를 strength 만큼만 섞는다. 1.0 은 GFPGAN 그대로 (피부가 매끈해지고 정체성이 떨어진다).

    추석 문어·복어 8명 실측 (antelopev2 원본 유사도): 없음 0.7012, 0.6 0.6828, 0.4 + face_only 0.6669.
    """
    _, _, r = restorer().enhance(img, has_aligned=False, only_center_face=False, paste_back=True, weight=0.5)
    return (img.astype(np.float32) * (1 - strength) + r.astype(np.float32) * strength).round().astype(np.uint8)


def face_only(swapped, original, face):
    """얼굴 파싱(피부·눈썹·눈·코·입) 안쪽만 교체 결과를 쓰고 바깥은 참고 이미지로 되돌린다.

    inswapper 의 붙여넣기 영역은 얼굴보다 넓어서, 얼굴 구멍이 뚫린 탈(추석 문어)에서는
    구멍 테두리의 검은 선을 덮어 흐릿한 띠로 만든다. 경계는 얼굴 높이의 1% 만 들이고 1.2% 로 페더한다
    (3%/2% 는 테두리는 더 깨끗했지만 원본 유사도가 0.02 더 떨어졌다).
    """
    from steps.retouch import FACE_LABELS, skin_mask
    fh = float(face.bbox[3] - face.bbox[1])
    m = skin_mask(original, face, labels=FACE_LABELS)
    k = max(int(fh * 0.01), 1)
    m = cv2.erode(m, np.ones((k, k), np.uint8))
    m = cv2.GaussianBlur(m, (0, 0), max(fh * 0.012, 1.0))[..., None]
    return (swapped.astype(np.float32) * m + original.astype(np.float32) * (1 - m)).round().astype(np.uint8)


def drop_forehead(swapped, original, face, keep=0.25, feather=0.18):
    """이마 위쪽을 원본 픽셀로 되돌린다.

    inswapper 의 붙여넣기 영역은 헤어라인까지 덮는다. 원본 인물에게 앞머리가
    있으면 그 잔상이 코스튬 후드 안에 어두운 띠로 남는다.
    눈썹 위는 어차피 후드라 되돌려도 잃을 게 없다.

    keep     눈높이 기준 위로 얼마나 남길지 (눈~입 거리 배수). 0 이면 눈썹선에서 자른다
    feather  경계 폭 (같은 단위). 넓을수록 부드럽다
    """
    import numpy as np
    kps = face.kps                      # 좌눈, 우눈, 코, 좌입, 우입
    eye_y = float((kps[0][1] + kps[1][1]) / 2)
    mouth_y = float((kps[3][1] + kps[4][1]) / 2)
    span = max(mouth_y - eye_y, 1.0)
    cut = eye_y - keep * span           # 이 위로는 원본을 쓴다
    band = max(feather * span, 1.0)

    H = swapped.shape[0]
    ys = np.arange(H, dtype=np.float32)[:, None]
    # cut 위쪽 0 -> 아래쪽 1 로 부드럽게
    a = np.clip((ys - (cut - band / 2)) / band, 0.0, 1.0)
    a = np.repeat(a, swapped.shape[1], axis=1)[..., None]
    return (swapped * a + original * (1 - a)).astype(swapped.dtype)


def crop_upper(img, face_bbox, above):
    """crop_reference 와 같은 함수를 써서 동일한 상반신 박스를 얻는다.

    안경 합성이 최종 좌표를 알아야 하므로 박스와 배율도 함께 돌려준다.
    """
    from steps.crop import upper_body_box
    H, W = img.shape[:2]
    box = upper_body_box(W, H, face_bbox, above=above)
    x0, y0, x1, y1 = box
    final = cv2.resize(img[y0:y1, x0:x1], (896, 1152), interpolation=cv2.INTER_LANCZOS4)
    return final, box, np.array([896 / (x1 - x0), 1152 / (y1 - y0)], np.float32)


def fit_box(img, margin: float = 0.04):
    """코스튬 전체가 들어가는 가장 작은 7:9 창. (box, 채움색)

    crop_upper 는 얼굴 기준으로 상반신만 잘라 키운다. 전신 실루엣이 콘셉트인
    코스튬(추석 물고기·윷 등)은 그러면 꼬리·다리가 잘린다. 여기서는 배경이 아닌
    픽셀 전체를 감싸는 창을 잡아 내용은 하나도 자르지 않고 빈 여백만 덜어낸다.
    창이 원본 밖으로 나가는 쪽만 테두리 색으로 채운다 — 전체를 통째로 여백 처리하면
    그라데이션 배경에 이음매가 생기고, 가장자리에 닿은 손이 뚝 잘린다.
    참고 이미지로 계산한다. 그래야 사전 누끼 마스크(steps/refmask.py)와 창이 같다.
    """
    H, W = img.shape[:2]
    ar = 896 / 1152
    edge = np.concatenate([img[0], img[-1], img[:, 0], img[:, -1]]).astype(np.int16)
    bg = np.median(edge, axis=0)
    fg = (np.abs(img.astype(np.int16) - bg).max(axis=2) > 40)
    rows = np.where(fg.sum(axis=1) > W * 0.004)[0]
    cols = np.where(fg.sum(axis=0) > H * 0.004)[0]
    if len(rows) == 0 or len(cols) == 0:
        rows, cols = np.array([0, H - 1]), np.array([0, W - 1])
    m = margin * max(H, W)
    bx0, bx1 = max(cols[0] - m, 0), min(cols[-1] + m, W)
    by0, by1 = max(rows[0] - m, 0), min(rows[-1] + m, H)
    ww = max(bx1 - bx0, (by1 - by0) * ar); wh = ww / ar
    cx, cy = (bx0 + bx1) / 2, (by0 + by1) / 2
    # 창을 원본 안쪽으로 밀어 넣되, 원본보다 크면 가운데에 둔다 (그 축만 여백이 생긴다)
    x0 = min(max(cx - ww / 2, 0), W - ww) if ww <= W else (W - ww) / 2
    y0 = min(max(cy - wh / 2, 0), H - wh) if wh <= H else (H - wh) / 2
    return (int(round(x0)), int(round(y0)), int(round(x0 + ww)), int(round(y0 + wh))), bg.tolist()


def render_box(img, box, fill):
    """fit_box 의 창을 896x1152 로 만든다. 돌려주는 scale 은 crop_upper 와 같은 규약 (kps_final = (kps - box[:2]) * scale)."""
    H, W = img.shape[:2]
    x0, y0, x1, y1 = box
    pad = [max(-y0, 0), max(y1 - H, 0), max(-x0, 0), max(x1 - W, 0)]   # 위 아래 왼 오
    win = img[max(y0, 0):min(y1, H), max(x0, 0):min(x1, W)]
    if any(pad):
        win = cv2.copyMakeBorder(win, *pad, cv2.BORDER_CONSTANT, value=fill)
    s = 896 / (x1 - x0)
    final = cv2.resize(win, (896, 1152), interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LANCZOS4)
    return final, np.array([896 / (x1 - x0), 1152 / (y1 - y0)], np.float32)


def fit_card(img):
    """fit_box + render_box. (final, box, scale) — crop_upper 와 같은 모양."""
    box, fill = fit_box(img)
    final, scale = render_box(img, box, fill)
    return final, box, scale


def prepare_source(src_img, gpu: int = -1):
    """원본에서 얼굴 하나를 고른다 (buffalo_l 팩 — inswapper 임베딩 공간). (face_app, face|None)"""
    fa = face_app("buffalo_l", ctx_id=gpu)
    sf = fa.get(src_img)
    return fa, (biggest(sf) if sf else None)


def swap_into(ref_path: pathlib.Path, src_img, src_path, source_face, fa, gl_mask=None, *,
              glasses_mode: str = "off", node: str = "http://127.0.0.1:8189", gl_steps: int = 20,
              bangs: str = "keep", keep: float = 0.25, restore: float = 0.0, gender: str = "male",
              crop: str = "upper", face_mask: bool = False):
    """참고 이미지 한 장에 원본 얼굴을 넣어 896x1152 카드를 만든다. 워커와 CLI 가 같이 쓴다.

    crop       upper(상반신만 잘라 키움, 직업·12지신) | fit(전체를 확대 없이 넣음)
    restore    GFPGAN 을 섞는 비율 0~1 (True 는 1.0). 0 이면 쓰지 않는다
    face_mask  얼굴 파싱 안쪽만 교체 결과를 쓴다 (얼굴 구멍이 뚫린 탈)
    돌려주는 것: (이미지 또는 None, "대상얼굴px|비고"). gl_mask 가 있으면 glasses_mode 대로 안경을 그려 넣는다.
    """
    from steps.crop import OVERRIDE
    tgt = cv2.imread(str(ref_path))
    tf = fa.get(tgt)
    if not tf:
        return None, "대상 얼굴 검출 실패"
    target_face = biggest(tf)
    fw = int(target_face.bbox[2] - target_face.bbox[0])
    res = swapper().get(tgt, target_face, source_face, paste_back=True)
    note = ""
    if bangs == "drop":
        res = drop_forehead(res, tgt, target_face, keep=keep); note += f"이마 복원 keep={keep} "
    if restore:
        w = 1.0 if restore is True else float(restore)
        res = restore_face(res, w); note += f"GFPGAN {w:g} "
    if face_mask:
        res = face_only(res, tgt, target_face); note += "얼굴만 "
    if crop == "fit":
        box, fill = fit_box(tgt)
        final, scale = render_box(res, box, fill)
    else:
        above = OVERRIDE.get(ref_path.stem, {}).get("above", 1.0)
        final, box, scale = crop_upper(res, target_face.bbox, above)
    if gl_mask is not None and glasses_mode != "off":
        kps = (np.asarray(target_face.kps, np.float32) - [box[0], box[1]]) * scale   # 크롭·확대 좌표로
        if glasses_mode == "paste":
            final = paste_glasses(final, kps, src_img, source_face.kps, gl_mask); note += "안경(paste)"
        elif glasses_mode == "kontext_crop":
            from steps.glasses_kontext import kontext_glasses_crop
            final = kontext_glasses_crop(final, kps, src_path, source_face.kps, gl_mask, node=node, steps=gl_steps); note += f"안경(kontext_crop {gl_steps})"
        elif glasses_mode in ("kontext", "auto", "on"):
            from steps.glasses_kontext import kontext_glasses
            final = kontext_glasses(final, kps, src_path, source_face.kps, gl_mask, node=node); note += "안경(kontext)"
        else:
            from steps.glasses_inpaint import inpaint_glasses
            final = inpaint_glasses(final, kps, src_path, source_face.kps, gl_mask, gender=gender, node=node); note += "안경(inpaint)"
    return final, f"{fw}px|{note.strip()}"


def main(a) -> int:
    out = pathlib.Path(a.out); out.mkdir(parents=True, exist_ok=True)
    # --src-file 이 있으면 그 파일을, 없으면 옛 data/faces/normalized/<source>.png
    src_path = pathlib.Path(a.src_file) if a.src_file else SRC_DIR / f"{a.source}.png"
    src_img = cv2.imread(str(src_path))
    if src_img is None:
        print(f"입력을 못 읽었다: {src_path}")
        return 1
    fa, source_face = prepare_source(src_img, a.gpu)
    if source_face is None:
        print(f"원본 {src_path} 에서 얼굴 검출 실패")
        return 1

    # inswapper 는 정체성 임베딩으로 얼굴을 새로 합성하므로 안경이 사라진다.
    # ArcFace 임베딩은 설계상 안경을 담지 않는다. 프롬프트가 없으니 픽셀로 옮긴다.
    # 모드: off | paste(픽셀 정렬 합성, 스티커 느낌) | inpaint(FLUX 로 눈 영역 다시 그림) | auto(=inpaint, 쓴 사람만)
    gl_mask = None
    if a.glasses != "off":
        r = glasses_ratio(src_img, source_face)
        wear = r > THRESH if a.glasses in ("auto", "inpaint", "kontext", "kontext_crop") else True
        print("안경 비율 {:.4f} -> {}".format(r, "반영" if wear else "없음"))
        if wear:
            gl_mask = glasses_mask(src_img, source_face)

    print("{:<10}{:>10}{:>10}  {}".format("동물", "대상얼굴", "소요", "비고"))
    print("-" * 44)
    # 레퍼런스가 성별로 갈려 있다. 같은 코스튬·포즈에 모델만 다르므로
    # 인물 성별에 맞춰 고르면 체형·머리 불일치가 줄어든다.
    if a.ref_dir:
        # 새 데이터 구조: data/reference/<컬렉션>/<male|female> 를 직접 준다
        REF_DIR = pathlib.Path(a.ref_dir)
        chosen = REF_DIR.name
    else:
        if a.set == "auto":
            g = json.loads((ROOT / "data" / "faces" / "gender.json").read_text(encoding="utf-8"))
            chosen = g.get(a.source)
            if chosen not in ("male", "female"):
                print(f"gender.json 에 {a.source} 가 없다. --set 으로 지정하세요")
                return 1
        else:
            chosen = a.set
        REF_DIR = DATA / a.collection / chosen
    print(f"레퍼런스 세트: {chosen}")
    for f in sorted(p for p in REF_DIR.iterdir()
                    if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")):
        name = f.stem
        if a.only and name not in a.only:
            continue
        t0 = time.perf_counter()
        final, note = swap_into(f, src_img, src_path, source_face, fa, gl_mask,
                                glasses_mode=a.glasses, node=a.node, gl_steps=a.gl_steps,
                                bangs=a.bangs, keep=a.keep, restore=a.restore, gender=chosen)
        if final is None:
            print("{:<10}{:>10}{:>10}  {}".format(name, "-", "-", note))
            continue
        cv2.imwrite(str(out / f"{name}.png"), final)
        fw, _, rest = note.partition("|")
        print("{:<10}{:>10}{:>10}  {}".format(name, fw, f"{time.perf_counter()-t0:.1f}s", rest))
    print(f"\n-> {out}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--collection", default="zodiac",
                   help="코스튬 모음 이름 (zodiac, jobs, ...)")
    p.add_argument("--set", default="auto", choices=["auto", "male", "female"],
                   help="레퍼런스 세트. auto 면 gender.json 의 인물 성별을 따른다")
    p.add_argument("--bangs", default="keep", choices=["keep", "drop"],
                   help="keep=그대로 (원본 앞머리의 흐릿한 잔상이 이마에 남는다) / "
                        "drop=이마를 코스튬 후드 픽셀로 되돌려 잔상을 지운다")
    p.add_argument("--keep", type=float, default=0.25,
                   help="눈높이 위 경계 위치 (눈~입 거리 배수). 작을수록 많이 지운다")
    p.add_argument("--gpu", type=int, default=-1, help="-1 이면 CPU")
    p.add_argument("--source", default="test1", help="넣을 얼굴 (data/faces/normalized)")
    p.add_argument("--src-file", default="", help="입력 얼굴 파일 경로 (있으면 --source 무시)")
    p.add_argument("--ref-dir", default="", help="레퍼런스 폴더 경로 (있으면 --collection/--set 무시)")
    # 기본은 끔. 붙여 보니 코스튬 사진에서는 어색했다 (2026-09-03 판단).
    # 화풍 쪽은 프롬프트로 넣어 자연스러워서 그쪽만 유지한다.
    p.add_argument("--glasses", default="off", choices=["off", "auto", "inpaint", "kontext", "kontext_crop", "paste", "on"],
                   help="안경 처리. auto/inpaint: 쓴 사람만 FLUX 인페인트(문장). kontext: 쓴 사람만 Kontext(참조사진). paste: 픽셀 합성. on: 무조건 인페인트")
    p.add_argument("--node", default="http://127.0.0.1:8189", help="인페인트용 ComfyUI 주소")
    p.add_argument("--gl-steps", type=int, default=20, help="kontext_crop 의 스텝 수")
    p.add_argument("--restore", action="store_true", help="GFPGAN 으로 얼굴 복원")
    p.add_argument("--only", default="", type=lambda s: set(x for x in s.split(",") if x),
                   help="참고 이미지 파일명(확장자 제외) 콤마 목록. 비우면 폴더 전부")
    p.add_argument("--out", default="outputs/zodiac/test1")
    raise SystemExit(main(p.parse_args()))
