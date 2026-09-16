"""BiRefNet-portrait cutout engine (CPU). Shared by the HTTP server and the batch CLI."""
import ctypes
import io
import os
import threading

from PIL import Image
from rembg import new_session, remove

MODEL = "birefnet-portrait"
# 모델 파일 이름(확장자 제외). int8 경량화 모델을 쓰려면 CUTOUT_MODEL_FILE=birefnet-portrait-int8 로 바꾼다.
MODEL_FILE = os.environ.get("CUTOUT_MODEL_FILE", MODEL)
# 추론 전에 긴 변을 이 크기로 줄인다. 모델 입력은 1024x1024 고정이라 품질 손실은 없고 후처리 메모리·시간만 준다.
MAX_SIDE = int(os.environ.get("CUTOUT_MAX_SIDE", "2048"))
# 추론 한 번에 필요한 추가 메모리(EC2 실측 최대 RSS 약 7.4GB, 유휴 약 1.3GB). 호스트 여유가 이보다 적으면 시작하지 않는다.
MIN_AVAILABLE_MB = int(os.environ.get("CUTOUT_MIN_AVAILABLE_MB", "6500"))
_session = None
_lock = threading.Lock()


class MemoryLow(RuntimeError):
    """호스트 가용 메모리가 부족해 추론을 시작하지 않았다. 호출자는 잠시 후 재시도한다."""


def available_mb() -> int:
    """호스트(컨테이너 공유) 가용 메모리 MB. /proc/meminfo 가 없으면 -1."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        pass
    return -1


def _release_heap() -> None:
    """추론이 끝난 뒤 glibc 가 쥐고 있는 빈 힙을 OS 에 돌려준다 (컨테이너 유휴 RSS 3GB -> 약 1.5GB)."""
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:  # non-glibc platform (e.g. Windows dev box)
        pass


def load_session():
    """Load the ONNX session once. rembg finds the bundled file via U2NET_HOME."""
    global _session
    with _lock:
        if _session is None:
            expected = os.path.join(os.environ.get("U2NET_HOME", ""), f"{MODEL_FILE}.onnx")
            if not os.path.exists(expected):
                raise FileNotFoundError(f"bundled model missing: {expected}")
            if MODEL_FILE != MODEL:
                # rembg 는 세션 클래스 이름으로 파일을 찾는다. 다른 파일(경량화 모델)을 같은 전/후처리로 쓰게 한다.
                from rembg.sessions import birefnet_portrait as _bp
                _bp.BiRefNetSessionPortrait.download_models = classmethod(lambda cls, *a, **k: expected)
            # onnxruntime CPU 메모리 아레나가 BiRefNet 추론에서 8GB+ 를 과할당해 컨테이너 OOM 을 낸다.
            # rembg 가 내부에서 만드는 SessionOptions 에 arena·mem_pattern 비활성·스레드 제한을 강제한다.
            import onnxruntime as _ort
            _orig_so = _ort.SessionOptions
            def _capped_so():
                o = _orig_so()
                o.enable_cpu_mem_arena = False
                o.enable_mem_pattern = False
                n = int(os.environ.get("OMP_NUM_THREADS", "2"))
                o.intra_op_num_threads = n
                o.inter_op_num_threads = 1
                return o
            _ort.SessionOptions = _capped_so
            try:
                _session = new_session(MODEL, providers=["CPUExecutionProvider"])
            finally:
                _ort.SessionOptions = _orig_so
            _session.model_path = expected
    return _session


def _limit_size(image: Image.Image) -> Image.Image:
    w, h = image.size
    side = max(w, h)
    if side <= MAX_SIDE:
        return image
    scale = MAX_SIDE / side
    return image.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)


def cutout(image: Image.Image) -> Image.Image:
    """Return an RGBA image with the background removed (long side capped at MAX_SIDE)."""
    sess = load_session()
    rgb = _limit_size(image.convert("RGB"))
    with _lock:  # one inference at a time keeps CPU/RAM use predictable
        avail = available_mb()
        if 0 <= avail < MIN_AVAILABLE_MB:
            # OOM 킬러에 죽어 재시작하는 것보다 즉시 거절하는 쪽이 다른 서비스에도 안전하다.
            raise MemoryLow(f"host has {avail} MB available, need {MIN_AVAILABLE_MB} MB")
        try:
            return remove(rgb, session=sess, post_process_mask=True)
        finally:
            _release_heap()


def cutout_bytes(data: bytes) -> bytes:
    """bytes (any PIL-readable image) -> PNG bytes with transparency."""
    img = Image.open(io.BytesIO(data))
    out = cutout(img)
    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
