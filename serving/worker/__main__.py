"""python -m serving.worker  — 경량 HTTP(/collections, /health)를 띄우고, 허용된 경우에만 RabbitMQ 소비를 시작한다.

공유 큐(전달서 4절)라 AI_WORKER_CONSUMER_ENABLED=false 가 기본이다. 전용 큐 또는 Dispatcher 가 확정되면 true 로 켠다.
"""
from __future__ import annotations

import logging
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("worker")

from serving.worker import config as C, consumer, http_api   # noqa: E402

if __name__ == "__main__":
    http_api.serve_in_thread()
    if C.CONSUMER_ENABLED:
        consumer.run_forever()
    else:
        log.warning("AI_WORKER_CONSUMER_ENABLED=false — /collections 만 서빙하고 큐는 소비하지 않는다")
        while True:
            time.sleep(3600)
