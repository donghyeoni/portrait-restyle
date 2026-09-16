"""그래프 빌더 자기점검. 서버 없이 돈다.

여기 담긴 항목은 전부 한 번씩 실제로 틀렸던 것들이다.
"""
from __future__ import annotations

import pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from engines.pulid import NEGATIVE, STYLES, build_graph

fails: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  OK   " if cond else "  실패 ") + msg)
    if not cond:
        fails.append(msg)


print("화풍 프리셋")
for name, st in STYLES.items():
    check("subject_male" in st and "subject_female" in st,
          f"{name}: 성별별 subject 가 있다")
    check(st.get("negative", "") == "",
          f"{name}: 네거티브가 비어 있다 (FLUX 는 cfg=1.0 이라 무시된다)")

print("그래프")
g = build_graph("x.png", style="vampire", seed=7, gender="female")
check(g["sampler"]["inputs"]["cfg"] == 1.0,
      "KSampler cfg 가 1.0 이다 (명세서의 3.0~3.5 는 FluxGuidance 값이다)")
check(g["guid"]["class_type"] == "FluxGuidance" and 2.5 <= g["guid"]["inputs"]["guidance"] <= 4.0,
      "FluxGuidance 가 2.5~4.0 범위다")
check(g["sampler"]["inputs"]["model"] == ["apply", 0],
      "샘플러가 PuLID 적용 모델을 받는다 (원본 UNET 이 아니라)")
check("noblewoman" in g["pos"]["inputs"]["text"],
      "female 이면 여성 호칭이 쓰인다")
check("modest" in g["pos"]["inputs"]["text"],
      "여성 프리셋에 노출 차단 문구가 포지티브로 들어간다")

print("파일명 접두사")
a = build_graph("x.png", filename_prefix="flux_pulid/aaa")["save"]["inputs"]["filename_prefix"]
b = build_graph("x.png", filename_prefix="flux_pulid/bbb")["save"]["inputs"]["filename_prefix"]
check(a != b, "요청마다 다른 접두사를 줄 수 있다 (인스턴스들이 output 을 공유한다)")

print("API 계약")
try:
    import inspect
    from serving import api_server
except ImportError as e:
    print(f"  건너뜀 (서버 의존성 없음: {e.name})")
else:
    gd = inspect.signature(api_server.generate).parameters["gender"].default
    # Form(...) 의 default 는 Ellipsis 가 아니라 PydanticUndefined 다.
    # Form("auto") 였다면 문자열이 들어온다 — 그걸로 가른다.
    check(not isinstance(getattr(gd, "default", None), str),
          "gender 가 필수 폼 필드다 (기본값을 두면 조용히 오판별된다)")

print()
print("전부 통과" if not fails else f"{len(fails)}건 실패")
raise SystemExit(1 if fails else 0)
