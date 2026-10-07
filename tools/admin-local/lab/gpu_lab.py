"""실험실 실행기. GPU 서버에서 돈다 (로컬 도구가 올려 두고 백그라운드로 실행한다).

    python gpu_lab.py <run_dir>

실제 실행은 serving/lab/runner.py 가 한다 — 운영 관리자 실험 워커와 같은 코드다. 로컬 도구가 이 파일 옆에
lab_runner.py 로 함께 올린다(GPU 서버 코드 폴더가 아직 그 코드를 갖고 있지 않아도 돌게).
이 파일은 요청·진행·결과를 run_dir 의 파일로 주고받는 일만 한다. 운영 코드·설정은 건드리지 않는다.

<run_dir> 에 읽는 것: request.json, input.png, (있으면) style_ref.png · template.png
request.json:
  engine        inswapper | pulid | kontext | original
  stylePreset   프리셋 코드. 없으면 직접 입력(kontext 는 prompt, inswapper 는 template.png 필수)
  gender        male | female | auto
  params        엔진별 조절값 (runner.PARAM_FIELDS)
  captureSteps  디퓨전 중간 단계 수 (0~6)
  env           {codeRoot, pylib, comfyInput, node} — 서버 경로. 로컬 설정에서 온다
남기는 파일: status.json(진행), analysis.png, step_N.png, reference.png, result.png, cutout.png, result.json
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import time
import traceback

HERE = pathlib.Path(__file__).resolve().parent
RUN = pathlib.Path(sys.argv[1]).resolve()
REQ = json.loads((RUN / "request.json").read_text(encoding="utf-8"))
ENV = REQ["env"]
os.environ["CUDA_VISIBLE_DEVICES"] = ""          # 얼굴 분석·GFPGAN·누끼는 CPU. GPU 는 ComfyUI 만 쓴다
os.environ["CUTOUT_PROVIDERS"] = "cpu"
CODE = pathlib.Path(os.path.expanduser(ENV["codeRoot"]))
sys.path[:0] = [str(HERE), str(CODE)] + ([os.path.expanduser(ENV["pylib"])] if ENV.get("pylib") else [])
os.chdir(CODE)
T0 = time.time()


def status(stage: str, **extra):
    (RUN / "status.json").write_text(json.dumps({"stage": stage, "elapsedMs": int((time.time() - T0) * 1000), **extra},
                                                ensure_ascii=False), encoding="utf-8")


def main():
    import engines.comfy as comfy
    if ENV.get("comfyInput"):
        comfy.COMFY_IN = pathlib.Path(os.path.expanduser(ENV["comfyInput"]))
    import lab_runner

    try:
        result = lab_runner.run(REQ, RUN, node=ENV["node"], ctx_id=-1,
                                on_stage=lambda name, label: status(name, label=label))
    except lab_runner.LabError as exc:
        status("failed", done=True, error=f"{exc.code}: {exc.message}", code=exc.code)
        return
    result["files"] = {"input": "input.png", "analysis": "analysis.png", "result": "result.png", "cutout": "cutout.png"}
    for name in ("style_ref.png", "template.png", "template_male.png", "template_female.png"):
        if (RUN / name).exists():
            result["files"][name.split(".")[0]] = name
    (RUN / "result.json").write_text(json.dumps(result, ensure_ascii=False, default=str), encoding="utf-8")
    status("done", done=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # 실패도 파일로 남겨 로컬 화면이 보여 준다
        status("failed", done=True, error=f"{type(exc).__name__}: {exc}", trace=traceback.format_exc()[-2000:])
