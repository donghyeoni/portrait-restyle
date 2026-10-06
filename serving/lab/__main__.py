"""python -m serving.lab — 화풍 실험실 워커 (GPU 서버, 실험 전용 GPU 의 ComfyUI 를 쓴다)."""
import logging

from serving.lab.worker import run_forever

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
run_forever()
