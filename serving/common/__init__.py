"""EC2 워커와 Cloud Run GPU 서비스가 함께 쓰는 부품.

  item.py        메시지·요청 파싱과 검증 (규약 3·4·7절), ItemError
  preprocess.py  얼굴 검출·임베딩·안경 판별 + 캐시 직렬화
  outputs.py     IMAGE / CUTOUT / THUMBNAIL 바이트와 메타데이터 (규약 5·6절)
  s3io.py        프리사인 URL 생성(boto3), URL 다운로드·업로드, 체크섬
"""
