# motion-cutout (CPU)

BiRefNet-portrait 인물 누끼 서비스. onnxruntime CPU 버전으로 동작하며 모델 파일을 이미지에 포함하므로 실행 서버에 인터넷이 없어도 됩니다.

## 준비

`models/birefnet-portrait.onnx` (973MB) 를 이 폴더 아래에 둡니다.

- 로컬 PC: `C:\Users\SSAFY\.rembg\models\birefnet-portrait\birefnet-portrait.onnx`
- JupyterHub 서버: `~/.rembg/models/birefnet-portrait/birefnet-portrait.onnx`

## 빌드

```bash
docker build -t motion-cutout:1.0 .
```

## 실행 (API)

```bash
docker compose up -d
curl -fs http://localhost:8001/health
curl -s -F "file=@photo.jpg" http://localhost:8001/cutout -o photo_cutout.png
```

응답은 투명 배경 PNG. 처리 시간은 응답 헤더 `X-Process-Seconds` 로 확인.
백엔드(Spring Boot)가 같은 EC2에 있으면 도커 네트워크로 `http://motion-cutout:8001/cutout` 을 호출하면 되고, 그 경우 `ports` 는 지워도 됩니다.

## 실행 (폴더 배치)

```bash
docker run --rm -v /path/in:/in -v /path/out:/out motion-cutout:1.0 python -m app.batch /in /out
```

## 이미지 전달 (레지스트리가 없을 때)

```bash
docker save motion-cutout:1.0 | gzip > motion-cutout-1.0.tar.gz   # 약 1.5GB
scp motion-cutout-1.0.tar.gz ubuntu@<BACKEND_HOST>:~
ssh ubuntu@<BACKEND_HOST> 'gunzip -c motion-cutout-1.0.tar.gz | docker load'
```

## 자원

- 대상 EC2: 16GB RAM (t3.xlarge 급, 4 vCPU). compose 기본값은 CPU 2개, 메모리 6GB 제한이며 백엔드·nginx·DB에 약 10GB가 남습니다.
- 메모리: 모델 로드 후 약 3~4GB. 제한을 4GB 이하로 내리면 큰 이미지에서 OOM 위험.
- CPU: 장당 처리 시간은 코어 수에 비례. 노트북 CPU 기준 8초/장, EC2 2vCPU 기준 15~30초/장 예상.
- 동시 처리는 1건씩 직렬 (엔진 내부 락). 대기열이 필요하면 백엔드의 ai-generation 작업 큐를 통해 호출.
