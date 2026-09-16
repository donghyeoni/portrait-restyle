# 결정 기록

## 2026-08-28

- 프로젝트명 `vangogh-portrait` -> **`portrait-restyle`**.
  고흐 외 화풍으로 확장할 계획이 확정되어, 화풍을 교체 가능한 단위로 재설계.
  디렉터리를 `data/styles/<name>/`, `outputs/<name>/`, `workflows/<name>/`로 분리.
- 입력을 증명사진으로 한정한 덕분에 **구조 제어 레이어(크롭 규격, ControlNet,
  InstantID)와 평가 스크립트는 화풍과 무관하게 재사용** 가능. 이것이 이 구조의 핵심 이점.
- 상업적 이용 없음으로 확정 -> Flux.1-dev(비상업 라이선스) 사용 가능.
  단 Phase 1 프로토타이핑은 8GB VRAM에서 스윕 속도를 확보하기 위해 **SDXL로 시작**하고,
  워크플로우가 잡힌 뒤 Flux로 교체한다.
- 1호 화풍 범위를 고흐 **초상화 계열**로 한정. 풍경화를 섞으면 스타일이 흐려지므로 제외.
- 모델 저장소를 ComfyUI 외부(`models/`)에 두고 extra_model_paths.yaml로 연결.

- **2호 화풍을 할로윈으로 확정.** 고흐와 변환 성격이 근본적으로 달라
  `style.yaml`에 `mode`(style_transfer / concept_transform),
  `regions`(영역별 변형 강도), `identity_threshold`(화풍별 정체성 기준) 필드 추가.
- 이에 따라 **베이스 워크플로우에 세그멘테이션 마스킹을 필수로 포함**하기로 결정.
  전체 화면 ControlNet 방식은 concept_transform에서 의상이 남아 실패한다.
- 할로윈은 참조 진품 셋이 없고 웹 수집은 라이선스 문제가 있는 반면
  베이스 모델이 개념을 충분히 학습하고 있으므로,
  **LoRA 학습 없이 프롬프트+IP-Adapter로 가능한지 먼저 검증**한다.

## 2026-08-28 (Phase 1 완료)

- **베이스 워크플로우 확정.** SDXL + ControlNet(depth/canny) + InstantID.
  파라미터 `d0.90 cn0.10 iid0.60`.
- **InstantID 가중치가 스타일-정체성 트레이드오프의 주 레버**임이 실측으로 확인.
  0.8 고정 시 스타일이 풀리지 않았고 0.4~0.6 에서 임파스토가 나왔다.
- **LoRA 없이도 실용적 품질에 도달.** 당초 "LoRA 필수"로 봤으나
  제어 레이어 균형만으로 style 0.29~0.30 달성. LoRA 는 상한을 더 올릴
  지렛대이지 전제는 아니다.
- **스윕은 여러 입력으로 돌려야 한다.** test1 단독 스윕이 과적합을 낳았다.

## 2026-08-31 (요구사항 변경 — 아래 2026-08-28 결정을 뒤집음)

**요구사항이 확정·확장됐다.**
얼굴 식별에 더해 **의상과 배경도 화풍에 맞추어야** 하고, 반짝이 등 효과도 필요하다.

- **세그멘테이션 미결 항목을 재개한다.** 의상과 배경에 서로 다른 내용이
  요구되므로 구분이 다시 필요하다. BiSeNet 의 흰옷 오분류(test3)가
  다시 문제가 되며, 인물 매팅(BiRefNet/RMBG) 도입을 재검토한다.
  (2026-08-28 에 "되살아날 수 있다"고 적어둔 그대로다)
- **배경 품질이 하드 요구사항이 됐다.** 현재 CLIP 0.0938 로 미달.
  InstantID 의 초상 구도 유지 성향이 유력한 원인이며,
  정체성 여유(임계값의 2.5배)를 배경 자유도와 교환해 검증한다.
- **영역별 채점을 추가한다.** 전체 CLIP 점수는 배경만 안 바뀐 경우를 놓친다
  (할로윈 1차: 전체 0.1825 vs 배경 0.0746).

## 2026-08-28 (이전 요구사항 — 2026-08-31 에 대체됨)

- ~~**할로윈은 얼굴 식별만 되면 된다.** 의상 보존은 요구사항이 아니다.~~
  -> `regions` 의 clothing/background 를 모두 1.0 으로 통일.
  -> **세그멘테이션 미결 항목이 해소됐다.** BiSeNet 이 흰옷(test3)을 배경으로
     오분류하는 문제는, 두 영역이 같은 강도로 교체되므로 결과에 영향이 없다.
     인물 매팅(BiRefNet) 도입 과제를 철회한다.
  -> 단, 향후 "코스튬은 유지하고 배경만 교체" 같은 요구가 생기면
     이 문제가 되살아난다. 로그(2026-08-28-phase1, 문제 7)에 근거를 남겨둔다.

## 2026-08-31 (스택 이관 — 위 결정 다수를 무효화함)

명세서(`T6_명세서/AI.markdown`)가 지정한 FLUX.1-dev + PuLID 로 갈아탔다.
**증명사진의 얼굴 임베딩만 쓰고 의상·배경·조명은 전부 새로 그린다.**
원본 옷·배경을 부분 변형해 살리려던 설계 자체를 접었으므로,
그 설계를 전제로 하던 미결 항목들이 함께 사라졌다.

해소된 미결:

| 항목 | 결말 |
|---|---|
| InstantID vs PuLID 택일 | **PuLID.** 정체성·피부 질감 모두 우위. 정체성 0.74~0.88 |
| 세그멘테이션 모델 선택 | **철회.** 얼굴만 쓰므로 의상/배경 마스크가 필요 없다 |
| 영역별 채점 | **철회.** 배경이 통째로 생성물이라 전체 채점으로 충분하다 |
| 배경 생성 품질 (CLIP 0.0938) | **해소.** 화풍 점수 0.19~0.27 |
| 색상 균질화 (정장이 전부 초록) | **해소.** ControlNet 을 걷어내면서 사라졌다 |
| 할로윈 LoRA 학습 필요 여부 | **불필요.** 프롬프트만으로 실용 품질 |

새로 생긴 결정:

- **성별에 따라 프리셋을 가른다.** `subject_male` / `subject_female`.
  `vampire` 가 "nobleman" 하나였을 때 여성 입력에 남성 복식이 붙었다.
  판별은 InsightFace `genderage`, 실패 시 `male` 로 떨어뜨린다 —
  남성에게 드레스를 입히는 실패가 반대보다 눈에 띄게 나쁘다.
- **로드밸런싱은 라운드로빈이 아니라 최소 부하.** 명세서는 라운드로빈을
  적었지만 생성 시간 편차(콜드 40초대 / 웜 13초대)가 커서 라운드로빈은
  느린 노드에 계속 배정한다.
- **요청마다 출력 파일 접두사를 다르게 준다.** 4개 인스턴스가 output
  디렉터리를 공유해 파일명이 충돌했다. 자세한 경위는
  [logs/2026-08-31-serving.md](logs/2026-08-31-serving.md).

## 미결

- **반짝이 등 효과(요구 d)** — 현재는 촛불·네온 보케와 장신구 반사 정도로만
  들어간다. 사용자가 이전에 테두리·오버레이 효과를 걷어내라고 했으므로
  화면 밖 장식이 아니라 화면 안 광원으로 푸는 방향이 맞아 보인다. 미검증
- **화풍별 LoRA 학습** — 프롬프트만으로 실용 품질에는 도달했다. 추가 이득이
  비용을 넘는지 미검증
- **뱀파이어 표지의 성별 편차** — 남성 결과에는 붉은 홍채가 나오는데
  여성 결과(test6)에는 안 나왔다. 원인 미확인


## 2026-08-31 (대조 시트를 만들고 나서)

- **test7 은 증명사진이 아니다.** 모든 화풍·모든 A/B 조건에서 최하위였고
  원인을 못 찾고 있었는데, 대조 시트를 만들어 원본을 나란히 놓자마자 보였다 —
  브이 사인에 회색 티셔츠, 방 배경의 스냅사진이다.
  파이프라인은 정면·단색 배경·상반신 증명사진을 전제한다.
  **점수표만 봐서는 못 찾았을 문제다.** 숫자는 "test7 이 낮다"까지만 말하고
  왜 낮은지는 말하지 않는다.
  -> test7 은 규격 밖 입력의 열화 정도를 보는 대조군으로 남긴다.
- **노출은 포지티브로 막는다.** `fantasy_noble` 의 여성 결과가 가슴이 파인
  가운으로 나왔다. 실제 인물의 증명사진에서 만드는 결과물이므로 막아야 한다.
  처음엔 공통 네거티브에 넣었는데 **효과가 없었다** — 아래 참조.
  `modest high neckline, fully covered shoulders` 를 포지티브에 넣었다.
- **네거티브 프롬프트는 쓰지 않는다 (`NEGATIVE = ""`).**
  FLUX.1-dev 는 `cfg=1.0` 이 필수인데 그러면 classifier-free guidance 가 꺼져
  네거티브 조건이 계산에 들어가지 않는다. 같은 시드로 기본/빈/정반대 세 가지를
  넣고 **픽셀 해시가 셋 다 동일**함을 확인했다.
  네거티브에 있던 의도는 전부 포지티브로 옮겼다.
  경위는 [logs/2026-08-31-serving.md](logs/2026-08-31-serving.md).
- **이미지가 같은지 볼 때는 픽셀을 해시한다.** ComfyUI 가 워크플로우 JSON 을
  PNG 메타데이터에 박기 때문에 파일 해시는 그림이 같아도 달라진다.
  이것 때문에 위 사실을 한 번 잘못 판정했다.

## 프롬프트 순서와 후처리

2026-09-02 에 AI 프로필 모델을 만들다 알아낸 것이다.
프로필 자체는 접었지만(2026-09-03) 아래 셋은 다른 모델에도 그대로 쓰인다.

- **머리에 쓰는 것은 프롬프트 맨 앞에 둔다.** 학사모가 안 그려져서 test1 로
  5가지를 재 봤다 (`outputs/_cap_sheet.png`).

  | 변형 | 결과 |
  |---|---|
  | 형태 묘사를 의상 뒤에 | 술만 나오고 판이 없다 |
  | 이름(`mortarboard cap`)을 맨 앞에 | 아무것도 안 나온다 |
  | **형태 묘사를 맨 앞에** | **제대로 쓴다** |
  | 위 + `empty space above the head` 프레이밍 | 모자가 화면 위로 잘린다 |
  | 위 + `pulid_weight 1.0` | 마찬가지로 잘린다 |

  갓 때와 같은 교훈(이름이 아니라 형태)에 하나가 더 붙는다 —
  **프레이밍 문구가 아니라 프롬프트 내 위치가 결정한다.**
  여백을 달라고 말하면 오히려 피사체를 밀어 올려 모자를 자른다.
- **색에 관한 지시는 프롬프트 맨 끝에 모은다.** `흑백` 컨셉이 컬러로 나왔다.
  `scene` 에 `black and white` 를 넣었는데 그 **뒤에** 붙는 공통 문구가
  `natural skin tone, subtle color grading` 이라 뒤에 온 쪽이 이겼다.
  색 지시를 `COLOR` 로 분리해 맨 끝에 두고, 흑백 컨셉이 그걸 덮어쓴다.
  프롬프트만으로는 미세하게 색이 새서 최종 출력에서 grayscale 로 한 번 더 민다.
- **GFPGAN 얼굴 복원은 쓰지 않는다 (`--restore 0` 이 기본).**
  같은 인물 6장으로 켠 것과 끈 것을 비교했다.

  | | 정체성 | 심미 |
  |---|---|---|
  | 후처리 없음 | 0.8398 | 6.41 |
  | GFPGAN(0.7) + 2x | 0.8247 | 6.41 |

  심미는 그대로인데 정체성만 0.015 깎였다. 확대해 보면 이유가 보인다 —
  모공을 밀어 피부가 밀랍처럼 된다 (`outputs/_post_compare.png`).
  얼굴이 주인공인 결과물에서 피부 질감은 "사진처럼 보이게" 하는 핵심이라 손해가 크다.
  ESRGAN 2배 업스케일만 남겼다.

## PuLID 가중치

2026-09-02 에 매거진 커버 모델을 시도하다 알아낸 것이다.
커버 자체는 접었지만 이 표는 다른 모델에도 그대로 쓰인다.

- **PuLID 가중치는 얼굴만 잡는 게 아니라 배경과 프레이밍까지 끌고 온다.**
  어두운 배경을 달라고 했는데 계속 레퍼런스 증명사진과 같은 밝은 회색이 나왔다.
  장면 문구를 프롬프트 맨 앞으로 옮기고, 시드를 바꾸고, guidance 를
  4 -> 7 -> 10 으로 올려도 **그림이 거의 그대로였다.** 범인은 PuLID 였다.
  test1 로 잰 값 (배경 밝기 = 좌상단 모서리 평균, 0 이 검정):

  | pulid | 0.6 | 0.8 | 0.9 | 1.0 | 1.1 | 1.3 |
  |---|---|---|---|---|---|---|
  | 정체성 | .704 | .828 | .845 | .874 | .868 | .868 |
  | 배경 밝기 | 3.7 | 6.5 | 13.3 | 32.8 | 84.6 | 188.8 |

  1.0 과 1.1 사이가 절벽이다. 1.3 에서는 어떤 프롬프트를 써도 배경이 안 바뀌고
  크롭도 증명사진처럼 가슴 위로 고정된다. **정체성마저 1.3 보다 1.0 이 높다** —
  낮추는 게 순손해가 아니다. 배경이나 구도를 프롬프트로 바꿔야 하는 작업이라면
  화풍이 쓰는 1.3 을 그대로 가져오지 말고 1.0 부근에서 다시 재라.
- **프롬프트 위치가 항상 먹히는 건 아니다.** 학사모는 맨 앞으로 옮기니 해결됐고
  커버 배경은 맨 앞으로 옮겨도 소용없었다. 위치는 *모델이 그 지시를 반영할
  여지가 있을 때* 쓰는 레버다. PuLID 처럼 다른 조건이 그 영역을 붙들고 있으면
  프롬프트를 어떻게 써도 못 이긴다. **안 움직이는 것 자체가 신호다.**

## 안경 (2026-09-03)

얼굴 임베딩은 안경을 담지 않는다. 화풍(PuLID)도 코스튬(inswapper)도 결과에서
안경이 사라진다. **inswapper 가 레퍼런스 픽셀을 얼굴에 얹는다고 착각했는데
아니다** — 정체성 임베딩으로 얼굴을 새로 합성하므로 안경이 남을 수가 없다.

두 모델의 성질이 달라 해법도 갈렸다.

- **화풍은 프롬프트로 넣는다 (유지).** `"metal-framed"` 만 쓰면 금테로 작게
  나온다. 화풍의 따뜻한 색보정이 더 밀어붙인다. 색과 크기를 형태로 못박아야
  원본(어두운 건메탈 큰 원형)에 가까워진다.
  프롬프트 내 위치는 끝·중간·맨앞 **전부 통했고** `pulid_weight` 1.3 에서도
  살아남는다. 커버 배경과 달리 PuLID 가 이 지시는 막지 않는다.
- **코스튬은 원본 픽셀을 정렬해 붙였다 (기본 끔).** 프롬프트가 없으니 5점
  랜드마크 상사변환으로 옮긴다. 실제 안경이라 화풍보다 정확한데, 코스튬
  사진에서는 어색해서 기본을 `--glasses off` 로 두었다.
  구현하면서 두 번 걸렸다:
  - **교체 직후에 붙이면 테가 사라진다.** 원본 얼굴 308px, 레퍼런스 얼굴 84px —
    3.7배 줄이면 얇은 테가 1픽셀 아래로 간다. 크롭·확대가 끝난 896x1152 에서
    붙여야 원본 해상도가 살아남는다.
  - **테만 오려내면 마스크가 조각난다.** BiSeNet 이 512px 에서 얇은 테를 온전히
    잡지 못한다. 마스크로 **위치만** 잡고 그 영역을 감싸는 타원을 부드럽게
    blend 한다. 같은 인물이므로 눈까지 함께 와도 되고 이음새도 안 보인다.
- **착용 여부는 판별한다.** 안 쓴 사람에게 씌우면 안 된다. BiSeNet 안경
  클래스(`eye_g`)를 피부 화소로 나눈 비율로 본다. 얼굴 크기에 무관해진다.
  실측 8명: 착용 1명 0.0904, 미착용 7명 전원 0.0000. 애매한 구간이 없다.

## 코스튬 안경 — 픽셀 합성에서 인페인트로 (2026-09-03)

원본 안경 픽셀을 정렬해 붙이는 방식(`paste`)은 안경 자체는 맞지만 **눈 주위에
밝기·선예도가 다른 패치가 남아 스티커처럼 보였다.** 사용자도 어색하다고 봤다.
같은 결과물(man008 cow·doctor·tiger)로 네 가지를 나란히 비교했다
(`outputs/_glalt_zoom.png`, `outputs/_glalt2_zoom.png`):

| 방식 | 결과 |
|---|---|
| A 타원 blend (paste) | 안경은 정확하나 패치가 티남 |
| B Poisson(seamlessClone) | 색은 맞춰지는데 테 대비가 사라져 흐릿 |
| C FLUX 인페인트 denoise 0.60 | 큰 얼굴은 자연스럽고 이음새 없음. 작은 얼굴은 안경을 못 만듦 |
| D~H 인페인트 0.70~0.80 | 0.80 부터 작은 얼굴에도 생김. guidance 6 이 테가 가장 어둡다 |

**FLUX 인페인트(눈 영역만 노이즈 마스크로 다시 그림, PuLID 로 정체성 고정)를
채택**했다. `denoise 0.80 · pulid 0.8 · guidance 6.0`. 장당 약 11초.
`--glasses inpaint`(= `auto`). `paste` 는 비교용으로 남겼다.

한계: 안경은 문장으로 전달되므로 종류(둥근 얇은 어두운 메탈테)만 맞는다.
레퍼런스 얼굴이 80px 남짓이면 테가 흐릿할 수 있다(tiger). 그래도 패치 자국은 없다.

## 코스튬 안경 — 인페인트(문장)에서 Kontext(참조 사진)로 (2026-09-03)

FLUX 인페인트는 안경을 **문장**으로만 전달해 금테로 흐르고, 눈 영역을 다시
그리면서 정체성을 깎았다. FLUX.1 Kontext [dev] 는 원본 사진을 **참조 이미지로
직접 보고** 그린다. 같은 눈 영역 마스크로 4장을 비교했다 (정체성 = ArcFace 코사인,
원본 대비):

| 코스튬 | inswapper(안경 없음) | A 인페인트(문장) | **B Kontext 눈영역** |
|---|---|---|---|
| doctor | 0.8428 | — | **0.8314** |
| cow | 0.8476 | 0.6408 | **0.8248** |
| tiger | 0.8519 | 0.7707 | **0.8119** |
| detective | 0.8402 | 0.4682 | **0.7920** |

B 는 정체성을 거의 잃지 않고(-0.01~-0.05), 테 색·두께가 원본에 가깝다
(tiger 의 두꺼운 검은 테까지 재현). **B 채택.** `--glasses kontext`, 러너 기본값.
장당 약 21초. 가중치 `flux1-dev-kontext_fp8_scaled.safetensors` 11.9GB 추가.

걸린 것 두 가지:
- **Kontext 에 코스튬+원본을 붙여 주고 빈 잠재에서 생성하면 원본 사진을 그대로
  베낀다** (정체성 0.94 가 나와서 알아챘다 — 너무 좋은 숫자는 의심해야 한다).
  작업 잠재는 코스튬 사진을 인코드한 것이어야 하고 마스크로 영역을 묶어야 한다.
- **마스크를 얼굴 전체로 잡으면 모자를 지어내고 정체성이 0.69 로 떨어진다.**
  눈 영역 타원으로 좁혀야 한다. 마스크 밖 inswapper 얼굴이 정체성을 지킨다.

얼굴 교체 계열(inswapper·SimSwap·GHOST 등)은 안경을 "대상의 속성"으로 정의해
학습돼 어느 것을 써도 원본 안경이 넘어오지 않는다. 참조 이미지 편집 모델
(Kontext·Qwen-Image-Edit·ACE++)이 유일한 길이다.

- **화풍 결과에는 Kontext 패스를 얹지 않는다.** man/007(사각테) hanbok·vampire 로
  시험했다 — 정체성 0.7887→0.7884, 0.8041→0.7985, 눈으로도 차이가 없다.
  화풍의 안경은 프롬프트(`GLASSES`)만으로 이미 원본 형태에 근접한다. 장당 21초를
  더 쓸 이유가 없어 코스튬에만 적용한다.

## GPU 할당 (2026-09-03)

**우리에게 할당된 GPU 는 device 1 하나다.** 나머지 0·2·3 은 다른 사용자 몫이다.
배치를 빨리 끝내려고 ComfyUI 를 4장에 띄웠던 것은 잘못이었다 — 스케줄러가 없는
공용 서버라 막아 주는 장치가 없고, 알아서 지켜야 한다. 런처(`GPUS=1`), 배치 러너
(노드 8189, `CUDA_VISIBLE_DEVICES=1`), API 서버 기본값을 전부 GPU 1 로 고정했다.
코스튬의 `--gpu 0` 은 "보이는 첫 장치" 이므로 `CUDA_VISIBLE_DEVICES=1` 아래에서만 GPU 1 이다.

## 화풍 5종이 인형·애니처럼 나온 원인 (2026-09-03)

28명 배치를 보니 cyberpunk 를 뺀 5종이 피부가 밀랍처럼 균일하고 윤곽이 둥글게
부풀어 있었다. 정체성 점수는 0.8 안팎으로 나쁘지 않은데 **사람이 보면 딴 사람**이다
— 점수가 잡지 못하는 종류의 실패다. cyberpunk 만 멀쩡했던 이유가 진단이 됐다:

- **`pulid_weight 1.3 · guidance 4.0`(기본) vs cyberpunk 의 `1.0 · 3.2`.**
  PuLID 를 세게 걸수록 얼굴이 "임베딩의 평균 얼굴"로 수렴해 질감이 사라진다.
  커버 작업 때 1.3 보다 1.0 이 정체성도 높았던 것(0.868 vs 0.874)과 같은 현상.
- **프롬프트의 `young`.** hanbok·kimono·qipao 주체 문구에 있었다. 나이는 임베딩이
  이미 담고 있는데 문장으로 강제하면 얼굴이 이상화·평탄화된다. 전부 지웠다.
- **얼굴이 화면에서 작다.** 허리 위 구도라 얼굴 픽셀이 적어 PuLID 가 넣은 얼굴이
  뭉개진다. cyberpunk 는 구도가 더 타이트했다. `FRAMING`(가슴 위, 얼굴 크게)을 넣었다.
- `QUALITY` 를 "raw unretouched photograph, visible pores and fine lines, subtle skin
  imperfections, realistic facial proportions, gentle shadow modelling, film grain" 으로
  바꿨다. 매끈함을 요구하는 단어를 없애고 결점을 요구한다.

기본값을 `pulid 1.0 · guidance 3.2` 로 내렸다. man/005·woman/006 × hanbok·vampire
5설정 스윕(`outputs/_stylesweep_sheet.png`)에서 시각적으로 확인했다. 점수는 아래.

| 인물 | 화풍 | A 1.3/4.0 | B 1.0/3.2 | C B+사진질감 | D C+얼굴크게 | E 1.1/3.5 |
|---|---|---|---|---|---|---|
| man/005 | hanbok | 0.8729 | 0.8842 | **0.8894** | 0.8886 | 0.8854 |
| man/005 | vampire | 0.8713 | **0.8731** | 0.8614 | 0.8549 | 0.8597 |
| woman/006 | hanbok | 0.8217 | 0.8265 | 0.8227 | 0.8154 | **0.8274** |
| woman/006 | vampire | 0.8349 | **0.8529** | 0.8271 | 0.8292 | 0.8246 |

B 는 4건 모두 A 이상. C 는 B 와 정체성이 거의 같으면서(±0.01~0.03) 눈으로는 가장
사진 같다(모공·음영). D 는 정체성을 가장 자주 깎았다. **C 채택** — pulid 1.0 ·
guidance 3.2 · 사진 질감 QUALITY, 구도는 그대로.

168장 전체를 이전 판과 새 판으로 채점한 결과 (ArcFace, 입력 대비, n=28/화풍):

| 화풍 | 이전 | 새 판 | 차이 |
|---|---|---|---|
| cyberpunk | 0.8158 | 0.8119 | −0.0039 (파라미터 동일, QUALITY 문구만 바뀜) |
| fantasy_noble | 0.8057 | 0.8404 | **+0.0347** |
| hanbok | 0.8316 | 0.8350 | +0.0034 |
| kimono | 0.8384 | 0.8399 | +0.0015 |
| qipao | 0.8346 | 0.8408 | +0.0062 |
| vampire | 0.8217 | 0.8317 | +0.0100 |
| **전체** | 0.8246 | **0.8333** | **+0.0087** |

정체성도 전반적으로 올랐다. 다만 168장 중 15장은 0.03 넘게 떨어졌다 — 시각 개선과
점수가 같은 방향이지만 장별 편차는 있다. 화풍 개선의 핵심은 점수가 아니라
"인형처럼 보이던 것이 사진처럼 보이게 된" 쪽이고, 점수는 그것이 정체성을 대가로
얻은 게 아님을 확인하는 용도다.

- **2026-09-03 관리자 허락으로 GPU 0·2·3 을 임시 사용.** 기본 설정은 여전히 GPU 1 이고,
  확장은 환경변수로만 한다 — `GPUS="0 2 3" bash serving/run_multi_gpu.sh` 로 띄우고
  `PORTRAIT_NODES=8188,8189,8190,8191 python scripts/run_dataset.py` 로 쓴다.
  작업이 끝나면 `bash serving/stop_extra_gpus.sh` 로 반드시 내린다 (8189 는 남김).
  화풍 168장 기준 32분 -> 약 8분.

## 입력 해상도 정규화는 하지 않는다 (2026-09-03)

"작은 입력을 ESRGAN 으로 키워 임베딩을 좋게 하자"는 가설을 재봤다.
- 28명 입력 얼굴폭(87~655px)과 화풍 정체성의 상관계수 **−0.45** — 얼굴이 클수록 낮다.
  가장 작은 두 명(87px·102px)이 0.84·0.87 로 상위권.
- 가장 작은 4명을 4x-UltraSharp 로 키워(87→366px 등) 재생성하니 **전부 떨어졌다**
  (−0.038, −0.052, −0.065, −0.051). 업스케일러가 지어낸 디테일이 임베딩을 바꾼다.
결론: 정규화·업스케일 없이 원본을 그대로 쓴다. `run_dataset` 이 `normalize_inputs.py` 를
건너뛰는 지금 상태가 맞다. (단, EXIF 회전은 업로드 단계에서 계속 바로잡는다.)

주의: 이 정체성 지표는 "입력 임베딩과의 코사인"이어서, 작은 입력은 임베딩 자체가
거칠어 생성 얼굴이 맞추기 쉽다는 편향이 있다. 그래도 업스케일이 도움이 안 된다는
결론은 4/4 로 일관돼 뒤집히지 않는다.

하위권: man/009 0.716, man/007 0.745 — 둘 다 안경 착용자·큰 입력. 안경 프롬프트의
정체성 비용을 다음에 잰다.

## 전통의상 남성의 긴 목 (2026-09-03)

한복·치파오·기모노 남성(여성은 한복만)에서 턱과 옷깃 사이가 벌어져 목이 늘어났다.
vampire·cyberpunk 는 깃이 턱 밑에 붙어 정상. 원인은 의상 문구가 **깃의 위치를 말하지
않아** FLUX 가 그 공간을 목으로 채우는 것. 형태로 못박았다(`NECK`):
"short neck, the collar closes high at the base of the neck right under the chin,
head sitting low between broad shoulders". 3명 x 3화풍 A/B/C 실측 — 정체성은
0.8622 → 0.8616 으로 변화 없이 목이 정상으로 돌아왔다. 프레이밍까지 바꾼 C(0.8601)는
차이가 미미해 문구만 넣는 B 를 썼다. 갓·학사모·배경과 같은 교훈 — 이름이 아니라 형태.

## 안경 판별기 — 큰 얼굴을 놓쳤다 (2026-09-03)

man/012(입력 1280x1680, 얼굴 545px, 둥근 얇은 메탈테)가 **0.0000** 으로 미착용 판정됐다.
BiSeNet 은 얼굴 크롭을 512 로 줄여 넣는데, 얼굴이 크면 축소 배율이 커져 얇은 테가
1픽셀 아래로 사라진다. 얼굴을 더 크게 잡은 크롭(pad 0.85)도 함께 보고 큰 값을 쓰는
다중 스케일(`glasses_ratio_ms`)로 바꿨다. 결과: man/012 0.2547, 그리고 역시 놓쳤던
**woman/012 0.2993** 이 새로 잡혔다(실제 착용자). 기존 3명은 0.21~0.35 로 여전히 뚜렷,
미착용 23명은 전원 0.005 미만. 임계 0.02 유지.
교훈: "작은 입력이 문제일 것"이라 짐작했는데 실제로 놓친 건 **큰 입력**이었다.
가정을 실측으로 확인하지 않았으면 두 명이 안경 없이 나갔다.

목 수정(NECK) 적용 후 재생성: 남 14명 x 한복·치파오·기모노 + 여 14명 한복 = 56장 +
man/012 화풍 6장. 시트 `outputs/_neck_after.png`.

- **판별기만 고치고 마스크 함수를 안 고쳐서 한 번 더 놓쳤다.** 코스튬 안경(Kontext)은
  `glasses_mask()` 로 눈 영역을 정하는데 이 함수는 단일 스케일 그대로였다. 큰 얼굴에서
  마스크가 비면 `eye_region_mask` 가 None 을 돌려 **인페인트를 조용히 건너뛰고 원본을
  그대로 저장**한다 — man/012·woman/012 코스튬 40장이 안경 없이 나갔다. 다중 스케일 +
  눈 랜드마크 타원 폴백을 넣어 마스크가 비는 경우를 없앴다. 같은 판별 로직을 두 군데에
  두면 한쪽만 고치는 일이 생긴다 — 공통 함수 하나로 모았다.

- man/012 화풍 6장은 17:13 재생성분이 안경 없이 저장돼 있었다(원인 미확인 — 판별·프롬프트
  경로는 정상이었다). `glasses=True` 를 명시해 다시 만들고 **눈 부위 확대로 직접 확인**했다.
  축소 시트만 보고 "들어갔다"고 판단한 것이 실수였다. 안경처럼 가는 요소는 확대로 검증한다.

## 웹툰풍 (4.ani) — 새 모델 (2026-09-03)

참고본(`data/output/4.ani/man/*.png`, 1792x2400)을 뜯어보면 **구도·머리·옷·안경이 사진
그대로**이고 선화 + 평면 셀 음영, 연한 그라데이션 배경이다. 즉 text2img 가 아니라 **구조를
보존하는 img2img 편집**이다. 어려운 점은 "애니처럼 그리기"가 아니라 "사진의 모든 것을
유지하면서" 그리는 쪽이다.

| 후보 | 판단 |
|---|---|
| **FLUX.1 Kontext dev** (이미지 편집) | **채택.** 구조 보존이 용도 자체이고 코스튬 안경에서 확인했다. 설치돼 있다 |
| FLUX dev + ControlNet(canny) + PuLID | 2순위. ControlNet 3.6GB 와 조립이 더 필요 |
| FLUX dev img2img + 애니 LoRA | 스타일은 강하나 옷·안경이 흔들린다 |
| SDXL 애니 체크포인트 + ControlNet | 걷어낸 스택 복구 — 마지막 수단 |

1차(3명 x 3문구, `outputs/_ani1_sheet.png`): Kontext 가 옷·안경·머리·포즈를 그대로 두고
웹툰화했다. "anime" 문구는 man/003 에 없는 안경을 지어내 탈락. "webtoon" 문구가 참고본에
가장 가깝고, 남은 차이는 얼굴이 조금 둥글고 어려 보이는 것·배경이 더 푸른 것.
2차에서 성인 비율·연한 배경으로 다듬었다(`outputs/_ani2_sheet.png`).
생성 896x1152 -> 4x-UltraSharp 2배 -> 1792x2304. `generators/ani/webtoon.py`,
러너 `--only ani`, 출력 `data/output/4.ani/{man,woman}/NNN.png`.

- **참고본을 스타일 예시로 옆에 붙여 주는 방식은 실패** (`outputs/_ani3_sheet.png`). Kontext 는
  화풍이 아니라 **내용**을 베낀다 — man/000 이 참조 인물의 빨간 줄무늬 넥타이와 얼굴형을 가져왔고,
  여성 둘은 거의 사진 그대로 남았고, 참조본의 ✦ 마크까지 따라왔다. 문장만 쓰는 현재 방식이
  구조·신원 보존에서 훨씬 낫다. 참고본과 똑같은 화풍이 필요하면 길은 하나 — 참고본 7쌍
  (사진↔일러스트)으로 Kontext LoRA 를 학습하는 것. 짝지어진 데이터가 있어 조건은 좋다.
- 문장을 더 다듬는 것(T1: 가는 선·각진 턱·탈채도)은 A2 와 거의 같고 오히려 woman/012 의 줄무늬
  셔츠를 흰 셔츠로 바꿔 놓았다 (`outputs/_ani4_sheet.png`). **프롬프트로 좁힐 수 있는 한계에
  닿았다.** 참고본 화풍에 더 붙이려면 LoRA 학습이다. 현재 결과물은 A2 로 둔다.

## 웹툰풍 LoRA — 이 프로젝트의 첫 가중치 학습 (2026-09-03)

지금까지 "자체 학습한 가중치는 없다"였다. 참고본과 같은 화풍은 프롬프트로도, 참조 이미지로도
닿지 않아 **FLUX.1 Kontext dev 위에 LoRA 를 학습**한다. README 의 문장은 결과가 나오면 바꾼다.

- 데이터: 참고본 7쌍(`data/input/man/NNN` 사진 ↔ `data/reference/4.ani/man/NNN` 일러스트)
  + 좌우 반전 = 14쌍. 768x1024 로 맞춤. 캡션은 내용 서술 + 트리거 `webtoon_style`.
  Kontext 는 (제어 이미지 -> 목표 이미지) 쌍 학습이 되므로 "이 사진을 이 그림으로"를 직접 배운다.
- 도구: ai-toolkit (별도 venv `~/ai-toolkit/venv`). 설정 `train/ani_lora/ani_webtoon_kontext.yaml`
  — rank 16, 1500 스텝, lr 1e-4, adamw8bit, bf16, 8bit 양자화, 250 스텝마다 저장·샘플.
- GPU: 빌린 GPU 0 (ComfyUI 8188 을 내려 확보). 학습 로그 `train/out/train.log`.
- 위험: 7장이 전부 남성·정장. 여성·캐주얼에 화풍이 덜 붙거나 남성 얼굴 특성이 스타일로 새면
  데이터를 늘려야 한다. 250 스텝 샘플로 조기 판단한다.
- 학습 데이터·산출물은 `train/` 에 두고 `.gitignore` 로 막았다 (실존 인물 사진).
- 학습 시작 후 데이터를 다시 보니 **man/002 쌍은 입력(캐주얼 티셔츠, 전신 스냅)과 목표(정장
  상반신)의 옷이 다르다** — 참고본 작가가 옷을 바꿔 그린 것. 이 쌍은 "옷을 정장으로 바꿔라"를
  가르칠 위험이 있다. 250스텝 샘플에서 옷이 바뀌는 징후가 보이면 002 를 빼고 재학습한다
  (`train/ani_lora_no002/`). 스텝 0 샘플(기본 Kontext)은 전원에 안경을 지어냈다 — LoRA 가
  이걸 잡는지도 본다. 속도 4.8s/step, 1500스텝 약 2시간.
- 250스텝: 스타일은 아직 기본 모델과 거의 같다(이른 시점, 500에서 재판정). 옷 변경 징후 없음.
  샘플 이미지의 안경은 **내가 샘플 프롬프트에 "and glasses" 라고 써 놓은 탓**이라 모델 판단에
  쓰면 안 된다 — 학습 캡션은 무관. 샘플 프롬프트를 고쳤다(다음 실행부터). 검증용 프롬프트를
  쓸 때는 그 문구가 결과를 편향시키지 않는지 먼저 본다.
- **500스텝 체크포인트를 ComfyUI 에서 실전 프롬프트로 직접 시험한 결과, LoRA 가 확실히 작동한다**
  (`outputs/_lora500_sheet.png`). 학습 샘플(편향된 프롬프트)로는 안 보였던 변화가 실전 조건에서는
  분명하다 — 선이 가늘어지고 얼굴이 성인형·각진 형태로, 머리에 결이 생기고 배경이 참고본처럼
  옅어졌다. 옷·넥타이·머리·포즈 보존은 그대로. 강도 1.0 과 1.5 모두 목표 쪽이고 1.5 는 여성에서
  조금 더 "그림"이 된다. 1500 스텝까지 진행하고 1000/1500 체크포인트를 같은 방식으로 비교해 고른다.
  교훈: **학습 샘플 프롬프트가 아니라 실제 쓰는 조건으로 판정한다.**
- 1000스텝 체크포인트(`outputs/_lora_cmp_sheet.png`): 강도 1.0 이 선 굵기·음영·배경 톤에서 목표와
  같은 계열이고 보존도 그대로. **1.3 은 과적용** — man/003 에 어두운 테두리 잡티, 배경이 어두워진다.
  강도는 1.0 으로 고정하고 1500 과 비교해 스텝만 고른다.
- 로컬 `data/output` 폴더가 `0.jobs_N` · `1.zodiac_N` · `2.style_R` · `3.motion_SSR` · `4.ani_SR` ·
  `5.weather_SSR` 로 바뀌어 있었다(사용자 명명, 접미어는 등급으로 보임). 서버는 접미어 없는
  이름을 그대로 쓰고, 내려받을 때 `pull_tree.py --dest` 로 로컬 이름에 맞춘다. 사용자 폴더명은
  건드리지 않는다. `5.weather_SSR` 은 비어 있는 새 폴더 — 다음 모델 자리로 보인다.
- **최종 채택: 1500스텝(최종 파일) × 강도 1.0** (`outputs/_lora_cmp_sheet.png`). 1000 보다 머리 결과
  얼굴 음영이 목표에 더 붙고 배경 톤이 같다. ×1.3 은 1500 에서도 배경이 회색으로 흐르고 과장돼 탈락.
  `models/loras/ani_webtoon_kontext.safetensors` 로 복사, `webtoon.py` 가 파일 존재로 자동 인식.
  이 파일은 실존 인물 참고본으로 학습한 것이라 저장소에 올리지 않는다.

## 사계절 실사 (5.weather) — 새 모델 (2026-09-03)

참고본(`data/reference/5.weather_SSR`, 3장): 얼굴·안경·머리는 사진 그대로 두고 **옷·배경·빛만
계절로 갈아입힌 실사** 야외 인물 사진(겨울 눈·회색 코트 / 가을 단풍·역광·갈색 니트 / 봄 벚꽃·핑크
니트). 사람당 1장, 896x1200(한 장 2배 업스케일).

A/B (`outputs/_wx1_sheet.png`): **PuLID 생성은 얼굴이 이상화돼 딴 사람이 된다**(woman/009). Kontext
편집은 안경·표정·머리까지 원본 그대로 유지하고 옷·배경을 바꿨다. **Kontext 채택.**
Kontext 의 문제 둘: 스튜디오 조명이 그대로 남아 계절 빛이 약하다 / 입력의 워터마크·로고가 따라온다.
둘 다 프롬프트로 지시했다("장면의 빛으로 다시 조명", "텍스트·로고·워터마크 제거") — 2차 결과
`outputs/_wx2_sheet.png`. 프리셋은 `generators/weather/seasons.py`(봄·여름·가을·겨울; 여름은 참고본이
없어 추정). 러너 `--only weather --seasons auto|목록`, 출력 `data/output/5.weather/{man,woman}/NNN.png`.
- 러너에 weather 블록을 넣는 편집이 **조용히 빠져** 첫 실행이 0장으로 끝났다("총 0s"). bash heredoc 안의
  파이썬 문자열에서 `\n` 이스케이프가 원문과 달라져 치환이 실패한 것 — 이번 세션에서 세 번째다.
  코드 블록 삽입은 Edit 도구(정확한 원문 매칭)로 하고, 실행 직후 "실제로 산출물이 생기는지"를 본다.
- 1차 28장은 **전원이 원래보다 늙어 보였고 man/002 는 피부가 나빠졌다.** 원인은 문구 —
  `visible pores`(모공 강조)와 "장면의 빛으로 재조명"이 야외 강한 빛과 만나 주름·거친 피부를 만들었다.
  사진관 사진에서는 모공이 "사진처럼 보이게" 했지만, 야외 계절 사진(SSR 등급)에서는 반대로 작동한다.
  같은 문구가 과제마다 다른 값을 낸다 — 컬렉션마다 따로 잰다. 개선안 비교 `outputs/_wx3_sheet.png`.
- **사람당 4계절 전부 만들기로 결정** (사용자). 출력은 `5.weather/{man,woman}/NNN/{spring,summer,autumn,winter}.png`
  112장. 러너 `--seasons spring,summer,autumn,winter`. 이전 1장짜리 `NNN.png` 는 새 결과가 생긴 뒤 치운다.
  GPU 4장 사용 허락(0·2·3 재기동) — 끝나면 내린다.
- **문구 3종 비교(`outputs/_wx3_sheet.png`)로는 man/002 피부가 안 나아졌다.** 원인은 문구가 아니라
  **입력 얼굴 크기**였다. 28명 중 man/002 만 전신 사진(얼굴 높이가 화면의 13%, 나머지는 27~63%).
  얼굴 196px 를 Kontext 가 1MP 로 그리면서 피부 결을 지어내 약한 여드름이 움푹한 흉터가 됐다.
  -> `frame()`: 얼굴이 화면의 1/4 미만이면 상반신으로 먼저 자르고, 짧은 변 700px 미만이면 4x-UltraSharp
  로 2배 키워 넣는다(`_wx4_sheet.png`, 개선되나 잡티는 남음).
- 크롭 뒤에도 남는 잡티는 **입력 피부 리터치**로 잡았다(`common/retouch.py`, `_wx5_sheet.png`). BiSeNet
  파싱으로 피부·코만 마스크(눈·눈썹·입·머리·안경 제외), 얼굴 크기에 비례한 양방향 필터 2회 + 1px 결
  복원, 강도 0.7. Kontext 가 입력 피부를 그대로 옮기므로 **후처리가 아니라 전처리**로 넣는 편이
  스티커 느낌이 없다. 모든 사람에게 적용한다(여자 "이쁘게" 요구와도 맞다).
- **남자가 늙는 문제는 문구 구조였다.** "젊게·면도" 문구나 추정 나이 삽입(`_wx6_sheet.png`)은 거의
  효과가 없었고, 세 가지 구조 비교(`_wx7_sheet.png`)에서
  - M 최소 편집 문구("배경을 ~로, 옷을 ~로 바꿔라. 얼굴·나이·피부는 그대로") -> **man/000·013 이 원본
    나이로 돌아왔다.** "photorealistic portrait photograph, 85mm, handsome" 같은 사진·외모 묘사가
    Kontext 에게 얼굴을 '인물 사진 모델'로 다시 그리게 해 10년쯤 늙고 수염 그늘이 생겼던 것.
  - G guidance 2.0 -> 미세한 차이.
  - F 얼굴 잠금(SetLatentNoiseMask 로 얼굴 화소 고정, 주변만 재생성) -> 얼굴은 그대로지만 스튜디오 빛이
    남아 붙인 듯하고 구도가 깨진다(woman/000). 탈락.
- 여자는 반대로 **이전 문구(부드러운 빛·맑은 피부·radiant)가 나이를 바꾸지 않으면서 더 예뻤다**
  (`_wx8_sheet.png`: woman/003·006 은 M 에서 밋밋해짐). 그래서 **문구를 성별로 나눴다**: 남자 M, 여자 이전
  문구. "Soft, flattering light" 한 구절 추가는 남자에서 차이가 없고 woman/012 를 나쁘게 해 빼는다.
  `visible pores` 는 두 성별 모두에서 제거. 러너 기본값을 4계절(`--seasons spring,summer,autumn,winter`)로.

## 웹툰풍 — woman/009 를 참고본 009-cutout 처럼 (2026-09-04)

목표 `data/reference/3.ani_SR/woman/009-cutout.png`(896x1200, RGBA 누끼): 현재 화풍보다 **선이 굵고 채도가
높은 애니 일러스트**(분홍빛 셀 음영, 윤기 나는 머리 하이라이트, 큰 눈), 옷은 핑크 니트로 바뀌어 있고
배경은 투명. 참고본이 1장뿐이라 LoRA 학습은 불가(같은 사람 1쌍을 학습하면 그 그림을 외울 뿐이다).

A/B (`outputs/_ani_w009_sheet.png`, woman/009 1명, GPU 0·1·2):
- 문구만으로 화풍이 상당히 붙는다. 핑크 니트 지시(S5·S6)가 목표에 가장 가깝다.
- **기존 웹툰 LoRA 를 0.6 으로 얹으면(S6·S7) 얼굴이 실제 인물에 더 붙는다**(눈동자 갈색, 이목구비 유지).
  LoRA 없음(S5·S9)은 눈이 보라색·더 애니 얼굴, 1.0 은 기존 화풍으로 끌려간다. 0.8 은 0.6 과 큰 차이 없음.
- 입력 오른쪽 아래의 작가 서명이 그대로 따라온다 -> "Remove any text, signature, logo or watermark" 로 제거(S7).
- 누끼: `rembg` 를 서버 venv 에 설치(`pip install rembg`, 모델은 `~/.rembg/models/`). BiRefNet-portrait 와
  ISNet-general 비교(`outputs/_ani_w009_edges.png`) — **BiRefNet 이 머리 가장자리에 흰 테가 없어 채택**,
  1장 약 1초(모델 로딩 제외). 흰 단색 배경으로 생성한 뒤 잘라내면 깨끗하다.
- 제안 파이프라인: Kontext + 스타일 문구(핑크 니트·서명 제거·흰 배경) + LoRA 0.6 -> BiRefNet 누끼 -> RGBA.
  아직 코드에 반영하지 않았다(사용자 결정 대기). 반영하면 `generators/ani/webtoon.py` 에 `--style cutout`
  같은 프리셋과 `common/cutout.py`(rembg 래퍼)로 넣는다.
- 교훈: 10분 넘는 서버 작업(pip 설치·모델 다운로드)은 Jupyter 커널에 직접 걸면 클라이언트 타임아웃과 함께
  커널이 지워져 중단된다. `setsid nohup` 으로 분리하고 로그를 폴링한다.

## 구조 재편 — 엔진 / 부품 / 컬렉션 매니페스트 (2026-09-07)

백엔드 연동 규약(docs/BACKEND_CONTRACT.md)이 "아이템 = stylePreset 코드 한 장, 코드 표의 주인은 워커 매니페스트"로
정해지면서 코드와 폴더를 그 기준으로 다시 짰다. 화풍이 계속 늘어날 예정이라 **컬렉션 추가 = yaml 한 장**이 목표다.

- `engines/` 처리 방식 3종만: `inswapper.py`(옛 costume/swap) · `pulid.py`(옛 style/flux_pulid) · `kontext.py`(옛 ani/webtoon 의
  그래프를 범용화) · `comfy.py`(제출·스테이징 공용). 잘 안 늘어나는 층.
- `steps/` 전처리·후처리 부품: faces · glasses · retouch · crop · upscale · cutout(rembg BiRefNet) · glasses_inpaint · glasses_kontext.
- `manifests/<id>.yaml` 컬렉션: id·tier·version·engine·enabled·reference·output·presets(코드 -> 키·문구). **PuLID 프리셋 문구를
  코드에서 yaml 로 옮겼다** (`concept.yaml`). 옮긴 뒤 git HEAD 의 옛 모듈과 비교해 6종 문구가 이번 세션 튜닝분(young 제거·NECK·
  QUALITY·GLASSES) 외에는 바이트 단위로 같음을 확인했다. 패키지 이름은 표준 라이브러리 `collections` 와 충돌하므로 `manifests`.
- `runner.py` 가 `scripts/run_dataset.py` 를 대신한다. 컬렉션별 if 분기 대신 `engine` 필드로 함수를 고르고, `--code DOCTOR` 처럼
  코드 단위로도 만든다. `--list` 가 백엔드에 주는 23개 코드 표(JSON)다.
- 서비스 범위: jobs(8) · zodiac(12) · concept(cyberpunk, vampire) · ani(webtoon) = 23. concept 의 나머지 4종과 weather 는
  `enabled: false` 로 정의만 남기고 결과물은 보존한다 (LLM API 담당).
- 데이터 폴더를 계약 이름으로 통일했다. 로컬 `0.jobs_N`·`1.zodiac_N`·`2.style_R`·`3.ani_SR`·`5.weather_SSR` -> `jobs`·`zodiac`·`concept`·
  `ani`·`weather`, 서버도 같은 이름. 등급 접미어는 매니페스트 `tier` 로. 웹툰 출력은 `NNN.png` -> `NNN/webtoon.png` (프리셋 = 파일).
  지운 모델(motion)과 옛 참고본(`data/faces`, `data/jobs`, `data/zodiac`)은 `data/_archive/` 로 옮겼다. 사진 파일은 이동만, 수정 없음.
- LoRA 파일에 버전을 붙였다: `models/loras/ani_webtoon_kontext/v1_1500.safetensors`. 서버의 `ani_test_*` 3개는 `train/out` 과 바이트
  동일한 복사본이라 지웠다. 가중치 목록은 `models/registry.yaml` (파일·용량·출처·라이선스; sha256 은 배포 전 채움).
- 지운 것: `serving/batch_generate.py` · `scripts/make_sheet.py` · `scripts/normalize_inputs.py` (옛 `data/faces/normalized` 흐름),
  `generators/weather/*` (매니페스트 + runner 로 흡수). 사용자의 서버 누끼 배치 스크립트는 `scripts/cutout_batch.py` 로 저장소에 들였다.
- 로컬에 웹툰 여성 14장과 concept man/007 cyberpunk 1장이 없어 서버에서 다시 내려받았다.
- 쓰지 않는 것은 **`manifests/backlog/`** 로 분리했다(처음 이름 `_unused` 를 실무 관용어 backlog 로 바꿈): `concept_legacy.yaml`(판타지 귀족·한복·기모노·치파오)과 `weather.yaml`.
  `concept.yaml` 에는 CYBERPUNK·VAMPIRE 만 남는다. 로더는 `backlog/` 를 `--include-disabled` 일 때만 읽고, PuLID 엔진의
  STYLES 도 `concept.yaml` 만 본다 — 서비스 코드 표(23개)에 안 쓰는 프리셋이 섞이지 않게. 되살릴 때는 블록을 옮겨 넣는다.
- backlog 은 **로컬 전용**이다. 서버·저장소에 올리지 않는다(push SKIP_DIRS, .gitignore). 서버의 weather 224장·concept 4종 224장(원본+누끼)도
  지워 서버 결과물은 서비스 코드 표와 같은 644장만 남겼다. 로컬 결과물·참고본은 `data/backlog/{output,reference}/` 로 옮겼다.
  이름 규칙: backlog = 추가 예정·미사용, `_archive` = 은퇴(지운 motion 모델·옛 참고본).
- 지운 매거진 커버 모델(옛 이름 motion)의 결과물·참고본도 `data/backlog/{output,reference}/magazine/` 로 옮겼다. 로컬의 `_archive` 는
  비어서 없앴다(서버 `data/_archive/` 에는 옛 코드·참고본이 남아 있음). 백로그 구성: concept 4종 · weather · magazine.

## 코스튬 안경 — 눈 영역 축소 방식은 탈락 (2026-09-07)

안경 착용자는 코스튬 20장마다 Kontext 로 안경을 그려 넣어 장당 20초, 한 사람에 7분이 걸린다(서비스 병목).
"눈 주변만 작은 캔버스(512x384)에서 다시 그리고 되붙이면 빠르고 안경에 픽셀도 더 간다"는 가설을
`steps/glasses_kontext.py::kontext_glasses_crop` 로 시험했다 (착용자 4명 x 코스튬 3종 x 3조건, `outputs/_gl_sheet.png`, `_gl_zoom.png`).

| 조건 | 장당 시간 | 결과 |
|---|---|---|
| 현재: 전체 캔버스 20스텝 | 20초 | 굵은 검은 테가 원본대로 재현. 12/12 안정 |
| 축소 20스텝 | 12초 | 8/12 는 현재와 같거나 더 선명. **man/008 tiger 는 가는 은테로 바뀜** |
| 축소 12스텝 | 7초 | man/008 tiger 안경 거의 사라짐. 여러 장에서 테가 얇아짐 |

- 시간 절감이 기대(3~5초)보다 작았다. 참조(코스튬+원본 스티치)를 FluxKontextImageScale 이 다시 1MP 로 키워 참조 잠재는 그대로 크기 때문이다.
- 품질은 대체로 유지되지만 **모자·장식이 크게 들어오는 코스튬(tiger)에서 테를 놓치는 경우가 생겼다.** 20장 중 한 장이라도
  안경이 사라지면 사용자에게는 실패라서, 8/12 성공률은 채택 기준이 아니다.
- 결론: **현재 방식(전체 캔버스 20스텝) 유지.** 처리량이 문제가 되면 "안경 버전 참고본 사전 제작"(GPU 0분, 표준 안경)으로 간다.
  축소 코드는 `--glasses kontext_crop` 옵션으로 남겨 두되 기본값이 아니다.

## 서비스 워커 구현 — EC2 워커 + Cloud Run GPU 서비스 (2026-09-08)

백엔드 검토 답(RabbitMQ `motion.ai` / `ai.generate.item` / `motion.ai.generate.item`, claim·progress·complete·fail 콜백, Cloud Run GPU 96GB
동기 호출, 프리사인 URL, `/collections` 경량 조회)을 규약 v3(docs/BACKEND_CONTRACT.md)로 반영하고 코드를 썼다.

- `serving/common/` — 메시지 파싱·검증(SUBJECT_MASK→CUTOUT 별칭 포함), 전처리 캐시(preprocess.json), 세 출력 생성(무손실 webp·RGBA·320px 썸네일),
  S3 프리사인/다운로드/업로드. EC2 와 Cloud Run 이 같은 코드를 쓴다.
- `serving/worker/` — EC2 워커. 큐 소비(prefetch 1) → claim → 하트비트 스레드(150초) → 처리 → complete/fail → **2xx 뒤에만 ACK**.
  콜백 실패는 NACK(requeue). 모르는 코드는 `UNKNOWN_STYLE_POLICY`(reject|fail) — 큐가 우리 전용인지 백엔드 확인 중.
  Cloud Run 호출은 429/5xx/타임아웃을 5·15·45초 백오프로 3회, 소진 시 `/fail`(retryable false, attempt 3).
  안경 착용자의 직업·12지신은 Kontext 가 필요해 **EC2 가 Cloud Run 으로 보낸다**(Cloud Run 서비스가 inswapper 도 갖는다).
- `serving/gpu/service.py` — Cloud Run 컨테이너. 시작 시 ComfyUI 자식 프로세스, `/health` 는 가중치와 무관하게 200, `/generate` 동기.
  가중치는 `MODELS_DIR`(GCS 볼륨). Dockerfile 은 CUDA 12.8 + torch 2.7.1 cu128(Blackwell sm_120).
- **TLJH 서버에서 도커 없이 검증**(가짜 S3 GET/PUT 서버 + START_COMFY=false): DOCTOR(안경 착용자) 36초 · CYBERPUNK 15초 · WEBTOON 30초,
  세 건 모두 업로드 바이트의 sha256·크기·해상도가 응답과 일치. 첫 시도에서 BiRefNet CUDA 세션이 공유 GPU 메모리 부족(822MB 할당 실패)으로
  죽어 `steps/cutout.py` 에 **CUDA→CPU 자동 대체**를 넣었다(Cloud Run 96GB 에서는 CUDA 그대로).
- 실행기 스크립트가 `pkill -f` 패턴에 자기 명령줄이 걸려 스스로 죽는 실수를 두 번 했다. 잡은 파일로 저장해 `bash file` 로 띄운다.
- 남은 것: EC2 워커를 가짜 RabbitMQ·가짜 백엔드로 로컬 시험, GCS 버킷·가중치 업로드(사용자 GCP 계정), Artifact Registry 빌드, docs/ARCHITECTURE.md.
- **EC2 워커도 TLJH 서버에서 통합 시험** (가짜 채널·가짜 백엔드 FastAPI·moto S3·가짜 누끼 서비스·Cloud Run 서비스 로컬 기동):
  DOCTOR(CPU 경로) claim→progress→complete→ACK 38초, CYBERPUNK(Cloud Run 경로) 13초, 모르는 코드(PIXEL_ART) NACK reject,
  같은 아이템 재수신은 S3 에 결과가 있어 0초에 complete(멱등). `SUBJECT_MASK` 별칭 처리, HMAC 헤더, preprocess.json 캐시 확인.
  RabbitMQ 전송 계층만 실환경에서 검증이 남는다.

## 백엔드 전달서 반영 — GPU 중립화, 콜백·재시도·출력 규칙 (2026-09-08)

백엔드가 v3 초안에 답한 "MOTION AI 엔지니어 연동 전달서"를 받아 규약을 v4 로 고치고 코드를 맞췄다. 바뀐 것:

- **Cloud Run 보류, GPU 환경 미정.** GPU HTTP 계약(`/health`, `/generate`)만 유지하고 워커의 GPU 어댑터를 업체 중립으로
  (`GPU_SERVICE_URL`, `GPU_AUTH_MODE` none|bearer|gcp-id-token, `GPU_AUTH_CREDENTIALS_FILE`, 연결 10초·응답 630초). `cloudrun.py` → `gpu_client.py`.
  `executionTarget` 은 `GPU` (구 `CLOUD_RUN_GPU` 호환). GCS·Artifact Registry·WIF 작업은 보류.
- **`/collections`**: Bearer 토큰(`AI_PRESET_CATALOG_TOKEN`), 모든 item 에 `rarity` 필수(매니페스트 `tier`), strong ETag = catalogVersion → 304.
  Host 의 127.0.0.1:8090 에만 바인딩. WEBTOON 의 rarity(SR)는 기획 확인 필요.
- **SUBJECT_MASK 를 CUTOUT 으로 바꾸지 않는다.** 요청받은 variant·objectKey·형식 그대로: SUBJECT_MASK 는 `subject-mask.png`(image/png, 누끼 알파를
  8비트 회색 PNG 로 — 내용 정의는 백엔드 확인 중). 할당 Target 이 2개(IMAGE·THUMBNAIL)면 2개만 만든다.
- **claim 응답 disposition**: CLAIMED / BUSY(→ `motion.retry.30s` 에 deferCount+1 재발행 confirm 후 ACK) / ALREADY_COMPLETED·ALREADY_FAILED·CANCELED(→ 작업 없이 ACK).
- **HMAC 규격**: `X-AI-Timestamp`·`X-AI-Nonce`·`X-AI-Signature("v1="+hex)`·`X-Trace-Id`·`X-Idempotency-Key`(complete/fail = callbackEventId),
  canonical = ts
nonce
POST
path
sha256hex(body). JSON 은 한 번만 직렬화해 서명·전송에 같은 바이트. 재전송은 ID·Body 유지, 서명 헤더만 갱신.
- **재시도**: 최초 포함 총 3회(번호 0·1·2), 소진 시 `/fail attempt: 2`. boto3 자동 재시도는 standard 2회로 제한. 재시도 대기 중에도 하트비트.
- **`/complete` DTO 필드만** (variant, bucketType, objectKey, contentType, fileSize, checksumSha256). width/height·metrics 는 보내지 않는다.
- **S3 native SHA-256**: EC2 업로드는 `put_object(ChecksumSHA256=base64)`. 프리사인 PUT 에 체크섬을 얹는 방식은 POC 전이라 **GPU 결과 바이트를
  EC2 가 받아(`returnBytes: true` → `outputs[].data` base64) SDK 로 올리는 relay 를 기본**으로 했다(`GPU_UPLOAD_MODE=relay`). 멱등 판정도 HeadObject 의
  native 체크섬이 있는 Target 만 "완료"로 본다.
- **공유 큐**: `AI_WORKER_CONSUMER_ENABLED=false` 기본. 처리 대상이 아닌 eventType·담당 아닌 코드는 requeue 한 번 뒤 **소비 중단**(임의 ACK·quarantine 금지).
  형식 오류(JSON·schemaVersion·필수 필드)만 reject → quarantine. 콜백 4xx(400/401/404/409/422/403)는 무조건 ACK 하지 않고 quarantine.
- **NO_FACE 자동 취소 없음**, 해당 Item 만 최종 실패. 전처리 캐시는 권한·정책 확정 전까지 **프로세스 메모리**만(`PREPROCESS_CACHE_S3=false`).
- GPU 없이 안경 착용자 코스튬이 오면 `GPU_UNAVAILABLE` 최종 실패로 보낸다 — 정상 처리로 표시하지 않는다(전달서 2절).
- 백엔드에 되물을 것: SUBJECT_MASK 의 내용(알파 마스크인지 RGBA 인지), WEBTOON rarity, 공유 큐 → 전용 큐 전환 시점, S3 서명 PUT POC 일정.
- **백엔드 회신(같은 날)으로 다섯 질문 모두 확정.** SUBJECT_MASK = 8비트 단일 채널 흑백 PNG(배경 0, 피사체 1~255) — 구현 그대로. WEBTOON rarity SR.
  `/collections` width/height 는 **최종 파일 크기** → 매니페스트 `output.upscale` 을 곱해 WEBTOON 1792x2304 (catalogVersion 변경). 공유 큐 소비 비활성 유지.
  relay 업로드가 공식 경로. 비밀값(RabbitMQ·S3·콜백·HMAC·토큰)은 배포 단계에 비밀 채널로.
- **배포 값 확정(백엔드 실서버 확인)**: RabbitMQ 는 Host loopback 이라 워커·cutout 을 **host network** 로 돌리고 카탈로그 8090·누끼 8001 을 127.0.0.1 에만 바인딩(`HTTP_BIND`,
  cutout command `--host 127.0.0.1`). 경로 `/opt/<project>/ai-worker`(compose), `/etc/<project>/ai-worker/worker.env`, `…/secrets/`. 배포는 백엔드가 직접(SSH 키 미전달).
  로컬에 Docker 가 없어 **이미지는 EC2 에서 빌드**하기로 하고, 워커 Dockerfile 이 빌드 중 InsightFace·inswapper 1.2GB 를 받아 `scripts/verify_models.py` 로
  `models/registry.yaml` sha256 과 대조하게 했다(저장소에 가중치 없음). registry 의 sha256 은 개발 서버에서 실측해 채운다. 절차는 `serving/DEPLOY.md`.

## 알파 테스트 GPU — 개발 GPU 서버 + SSH 역터널 (2026-09-08)

정식 GPU 환경(백엔드 공동 결정, 후보 AWS EC2 g6e.xlarge 서울)은 알파 결과를 보고 정하기로 했다. 알파는 **개발 GPU 서버를 그대로 GPU 모델 서버로** 쓴다.
가중치 40GB 와 ComfyUI 가 이미 상주해 업로드·인스턴스·할당량 대기가 없고 비용이 0 이다.
- 인바운드 불가 → `serving/gpu/tunnel.sh` 가 22 아웃바운드로 EC2 에 `ssh -R 127.0.0.1:18080:127.0.0.1:8080` 역터널을 유지(bash 재접속 루프, autossh 불필요).
  EC2 쪽은 셸 없는 터널 전용 사용자(`restrict,port-forwarding,permitlisten`)만 있으면 되어 백엔드의 "SSH 키 미전달" 원칙과 충돌하지 않는다.
- `serving/gpu/run_tljh.sh` 가 60초마다 ComfyUI(8189)·서비스(8080)를 확인해 죽었으면 다시 올린다(9/4 처럼 관리자가 정리해도 복구).
- 서비스에 `GPU_SERVICE_TOKEN` Bearer 인증을 넣었다(워커 `GPU_AUTH_MODE=bearer`). GPU 계약은 그대로라 정식 서버로 옮길 때 워커는 URL 만 바뀐다.
- 한계: 공유 서버·SLA 없음·동시성 1. 서버 권한은 우리에게 있어 별도 허락은 불필요(처음엔 필요하다고 봤으나 정정).


## 백엔드 콜백 프록시·HMAC 외부 확인 (2026-09-10)

`https://<BACKEND_HOST>` 에 외부에서 직접 호출해 확인했다(비밀값 없이, 더미 키).
- Nginx `/internal/v1/ai-generation-items/**` 프록시 보완 완료: POST 가 Spring 까지 도달한다(GET 은 403 으로 차단).
- 헤더 검사: `X-AI-Timestamp` 누락 → 400 `REQUEST_HEADER_REQUIRED`.
- 서명 검증: 잘못된 키 → 401 `AI_CALLBACK_AUTHENTICATION_FAILED`. claim/progress/complete/fail 네 엔드포인트 모두 동일.
- 타임스탬프 창: 1시간 전 epoch 초 → 401 `AI_CALLBACK_TIMESTAMP_EXPIRED`. 현재 epoch 초는 통과해 형식(초)이 규약과 일치.
- 외부에서 확인 불가: EC2 워커·누끼 컨테이너(127.0.0.1 바인딩), RabbitMQ, S3 권한, GPU 역터널. 백엔드 확인 또는 교육장 접속 필요.

## 알파 GPU 서비스 가동 (2026-09-10)

교육장 복귀 후 알파 파일 78개 푸시, `~/.config/portrait/gpu_token`(0600) 발급, `run_tljh.sh start`. ComfyUI 8189·서비스 8080 정상.
`/generate` 토큰 없음 → 401. 서버 내부에서 CYBERPUNK 1건 실호출: 200, 37.4s(모델 로드 11.7s), IMAGE 896×1152·CUTOUT·THUMBNAIL 249×320 체크섬 일치, promptTemplateVersion `concept-v4`.
남은 것: EC2 터널 사용자(백엔드) → `tunnel.sh start`, 토큰 비밀 채널 전달.

## 알파 실제 구성 — 전부 GPU 서버, RabbitMQ 만 정방향 터널 (2026-09-10)

백엔드와의 연동은 계획(EC2 워커 + 역터널)과 다르게 **워커·누끼·GPU 서비스를 모두 개발 GPU 서버 프로세스로** 올리고,
EC2 의 RabbitMQ 만 `ssh -L 5672:127.0.0.1:5672 motion-tunnel@<BACKEND_HOST>` 로 끌어오는 구성으로 가동됐다(9/9 12:58 터널, 9/10 13:14 워커 소비 시작).
콜백·S3 는 443 아웃바운드로 직접 나가므로 역터널·EC2 컨테이너·`aitunnel` 사용자가 필요 없어졌다. `serving/gpu/tunnel.sh` 의 `-R` 설계와 EC2 배포 안내는 정식 환경용으로 남긴다.
E2E 1차: 25 아이템 전부 complete, fail 0. 사용자 1명 23장 7분 51초(CPU 20장 6.5분, GPU 3장 103초). CPU 구간이 판정 기준(3분) 미달 → INSWAPPER_CTX GPU 사용·누끼 provider 정리·동시 2 를 순서대로 시험.
운영 위험: 터널·워커·누끼가 nohup 단독 프로세스라 재접속·재기동 감시가 없다. `run_tljh.sh` 감시 대상에 추가하는 것이 다음 작업.

## 직업·12지신 누끼 = 참고 이미지 사전 마스크 (2026-09-10)

프로파일(서버, CPU 경로 1장): 얼굴 검출·교체 0.5초, **BiRefNet CPU 누끼 14.6초**, webp 무손실 2장 1.0초 -> 아이템 19초의 3/4 이 누끼.
관찰: inswapper 는 얼굴 상자 안만 바꾸고 크롭 박스는 참고 이미지 얼굴 위치로 정해지므로 누끼 경계는 사용자와 무관하게 고정.
결정: 참고 이미지마다 마스크를 한 번만 만들어 `<ref>.mask.png`(8비트 L, 896x1152) + `<ref>.mask.json`(ref_sha256, above, box) 으로 옆에 두고,
워커·GPU 서비스의 inswapper 경로는 `steps/refmask.cutout_fn()` 으로 마스크를 얹는다(0.01초). 마스크가 없거나 sha256/above 가 다르면 누끼 모델로 떨어진다(안전망).
생성: `scripts/precompute_masks.py` (BiRefNet CPU, 장당 7~15초, 40장). 참고 이미지·`above` 를 바꾸면 다시 돌린다(`--force`).
안경 착용자(Kontext 안경 보정)도 눈 주변만 바뀌므로 같은 마스크. 컨셉·웹툰은 매번 새 그림이라 해당 없음 -> 그쪽은 빈 GPU 로 누끼 이동이 답.
사고 기록: 첫 구현이 `with_suffix` 로 `<stem>.png` 를 만들어 참고 이미지와 stem 이 겹칠 수 있었다(직업 참고가 .jpg 라 덮어쓰기는 없었음). 즉시 중단·삭제 후 `with_name` 으로 고침.
기대: CPU 아이템 19초 -> 약 4초(교체 0.5 + 인코딩 1 + S3·콜백 3). 반영은 워커 재시작 시점에.

재시작 (2026-09-10 15:23): `scripts/restart_worker.py` 로 워커를 같은 환경변수로 재기동(pid 543987, 소비 재개), GPU 서비스는 pkill 후 keeper 가 6초 만에 복구. 누끼 8001·터널 5672 는 그대로. 다음 백엔드 발행분부터 사전 마스크 적용 — 5.4 재측정 예정.

## 워커 병렬 = 큐 분리 풀 (발견, 2026-09-10)

시간 최적화 중 서버에 이미 큐 분리 구성이 가동 중임을 확인했다: 백엔드가 `...generate.item.cpu`/`.gpu` 로 나눠 발행하고,
CPU 워커 4개(`gpu-worker-cpu-01~04`)·GPU 워커 1개(`gpu-worker-gpu-01`)가 각 큐를 소비한다. 워커는 `RABBIT_QUEUE` 환경변수로만 갈리며 코드는 동일(사전 마스크 반영본).
그래서 별도로 워커를 추가하지 않았다(`scripts/scale_workers.py` 가 3개 이상 감지 → 무동작). 실측: CPU 아이템 총시간 중앙 2.7s(이전 18~24s).
성능 근거는 `docs/PERF_LOG.md`, 원자료 `logs/perf/`(로컬). 중복 `gpu-worker-cpu-01` 2프로세스는 정리 권장으로 보고만 하고 손대지 않음.
벤치(스레드, 계산만): 워커1 makespan 92.2s → 워커3 71.4s(−23%), 첫 카드 13.5→1.9s. 남은 벽은 GPU 직렬 ~65s.

## Gemini = Developer API (2026-09-10)

Gemini 프리셋(우리 이미지 파이프라인 밖, 백엔드/LLM 코드 담당)의 API 는 **Google Gemini Developer API**(AI 스튜디오 키, generativelanguage) 로 정했다. Vertex AI 아님.
근거: 알파 규모, 인프라가 AWS 라 GCP 프로젝트·IAM 신설 회피, 키 하나로 즉시 연동·무료 등급. `google-genai` SDK 라 필요 시 환경변수만으로 Vertex 전환 가능.
우리 저장소에 Gemini 호출 코드는 없다(ComfyUI 내장 gemini 노드는 미사용). 실제 호출 위치·담당은 백엔드와 확정 필요.

## 컨셉·웹툰 누끼를 GPU 서비스 밖 CPU 로 (2026-09-10)

설계 원칙(GPU 는 그림만, 누끼는 CPU)에 맞춰, GPU 서비스가 하던 in-process 누끼를 워커(CPU)로 옮겼다. 단일 GPU 처리량을 높이는 핵심.
- `GPU_IMAGE_ONLY=true` 면 서비스는 그림만 반환(`image` 필드, PNG base64). 누끼·인코딩·업로드·SUBJECT_MASK 는 워커가 `outputs.build` 로 처리.
- 웹툰(1792×2304) 누끼는 `cutout_downscaled`: 1200px 로 줄여 누끼 뜨고 마스크만 확대. **실측 16.0→13.4초(IoU 0.9962)** — BiRefNet 은 입력을 내부 고정 해상도로 리사이즈해 CPU 누끼가 해상도와 무관하게 ~14초라, 저해상도화 이득은 2.6초뿐(품질 동일해 유지).
- 안경 코스튬(inswapper, 현재 `GLASSES_TO_GPU=false` 라 비활성)은 참고 이미지 사전 마스크를 워커가 사용.
- **정정된 트레이드오프.** CPU 누끼는 장당 ~14초(GPU 누끼는 ~1~2초). 그래서 이 오프로드는 **GPU 큐 워커 2개 이상**과 짝일 때만 이득: 워커 A 가 CPU 누끼(14초) 도는 동안 워커 B 가 GPU 서비스에 다음 그림을 시켜 GPU 가 계속 그린다. **GPU 워커 1개면 누끼 14초 동안 GPU 가 놀아 오히려 손해.**
- 효과(GPU 워커 2개 전제): GPU 는 그림만(사용자당 ~46초) → 시간당 56→79명(+40%), GPU 누끼 모델(~22GB) 제거로 OOM 해소.
- 대안(설계 A): 누끼를 GPU 에 두되 CUDA 아레나를 ~3GB 로 상한. 누끼 1~2초 유지, GPU 워커 1개로 단순, ~72명/시간. B 보다 지연 낮고 단순하나 처리량 천장 약간 낮음. 둘 다 22GB 문제는 해결.
- 호환: 워커는 `image` 응답이면 새 경로, `outputs` 응답이면 구버전 경로 → 롤링 재시작 안전. 구버전 서비스와도 동작.
- 배포는 라이브 테스트가 조용한 시점에 워커·서비스 재시작으로. `docs/PERF_LOG.md` 에 배포 후 재측정.

## 오프로드 배포·측정 결과 (2026-09-11)

`GPU_IMAGE_ONLY=true` 배포(GPU 서비스 재시작 + GPU 큐 워커 2개). device1 메모리 42→20GB(누끼 22GB 제거). 측정:
- 워커 2개: makespan 84.5s(GPU 유휴 — CPU 누끼 15~20s > 그림 11.5s 라 둘 다 누끼 도는 동안 GPU 놀음).
- 워커 3개: 70.5s(GPU 45.6s 연속, 유휴 없음). GPU 점유/사용자 65→46s → 시간당 55→78명(+42%).
- 단일 사용자 지연은 65→70s 소폭↑(CPU 누끼가 느린 대가). 이 변경은 다중 사용자 처리량용.
결론: **GPU 큐 워커 3개 이상**이라야 이득 실현. 현재 2개 → 3개째 추가 필요. `scripts/scale_workers.py` 는 CPU 큐용이라, GPU 큐 워커 추가는 `deploy_offload.py` 방식(전용).
대안(설계 A: 누끼 GPU 유지 + 메모리 상한)은 단일 지연 낮고 워커 1개로 단순하나 처리량 천장 낮음(~72명). 다중 사용자면 현행(B) 유지가 낫다.

## EC2 배포 진행·BiRefNet 메모리 한계 (2026-09-11)

프로님 지시로 워커·누끼를 EC2(<EC2_HOST>, Ubuntu, 4코어/15GB, 백엔드·postgres·redis 공유)로 이전 시작. GPU 서버는 그림만.
완료: 이미지 2개 빌드(portrait-worker:v1 CPU, motion-cutout:1.0), 컨테이너 5개 기동, worker.env(비밀값·AWS 키·GPU 토큰), 역터널(EC2 127.0.0.1:18080→GPU 8080), RabbitMQ 로컬·카탈로그·헬스.
직접 테스트(man/000): 직업/doctor EC2 CPU 13초 OK. 컨셉 CYBERPUNK EC2→역터널→GPU 그림 11.8초→반환 OK. **역터널·GPU 생성 검증됨.**
**막힘: 컨셉·웹툰 누끼(BiRefNet CPU)가 입력 크기와 무관하게 ~7~8GB RAM 을 써서 15GB EC2 에서 OOM.** 512 로 줄여도 동일(모델이 내부 고정 해상도). arena·mem_pattern off 로 메모리는 줄었으나(엔진 수정) 8g 에서 43초로 느려짐, 6g 에선 OOM.
결론: **이 EC2(15GB)에는 BiRefNet 누끼가 안 맞는다.** 직업·12지신(사전 마스크, BiRefNet 불필요)은 EC2 에서 정상.
선택 필요:
- A. 하이브리드 — 컨셉·웹툰 누끼는 GPU 서버(46GB, 6.5초)에서. 직업·12지신은 EC2. GPU 서비스가 컨셉·웹툰 누끼까지(오프로드 부분 원복) 또는 컨셉·웹툰 워커를 GPU 서버에.
- B. EC2 에 경량 누끼 모델(u2net 등) — 메모리 1GB 대, 품질 하락.
- C. EC2 RAM 증설.
추천 A. (프로님 "전부 EC2"는 BiRefNet 메모리 때문에 이 인스턴스에선 불가.)

## EC2 하이브리드 배포 완료·V1 대기 (2026-09-14)

**배포 완료(EC2 <EC2_HOST>):** portrait-worker:v1·motion-cutout:1.0 빌드, 워커 4개 + 누끼 컨테이너 기동. worker.env(비밀값·AWS 키·GPU 토큰, 값은 GPU 서버 워커에서 이관). 역터널 GPU서버→EC2(127.0.0.1:18080). RabbitMQ 로컬. S3 자격증명은 env AWS 키 + AWS_ENDPOINT_URL_S3(실 AWS), 버킷 정책이 지정 키 경로만 Put 허용(임의 키 테스트는 403이 정상).
**하이브리드 확정:** BiRefNet 누끼가 입력 크기 무관 ~7~8GB RAM → 15GB EC2(백엔드 공유)에 안 맞음. 그래서 `GPU_IMAGE_ONLY=false`: 컨셉·웹툰은 GPU 서비스가 그림+누끼 완성본 반환(GPU 서버 46GB), EC2 워커는 업로드만. 직업·12지신은 EC2 CPU(inswapper+사전 마스크, BiRefNet 불필요). 직접 테스트(man/000)로 두 경로 다 확인.
**백엔드 V1/V2 변경(공백 기간):** 백엔드가 v1(레거시) 비활성, v2 요구로 바뀜. `/ai-generations`→409 AI_LEGACY_REQUEST_DISABLED, `/ai-generations/alpha`→503 AI_WORKER_V2_UNAVAILABLE.
- V1 재개 스위치 `ALPHA_LEGACY_GENERATION_ENABLED`(신규, MR!172, develop 머지, 기본 false) — 백엔드가 서버 env 설정+재시작해야 열림. **백엔드 대기 중.** 열리면 상태조회 결과이미지 마스킹도 같이 열림(아니면 resultImageUrl null).
- V2 가용성은 `motion.alpha.worker-v2-enabled` 설정 하나(기본 false). 하트비트/등록 없음. 503은 롤아웃 스위치 off 의미.
- **워커를 v1 base 큐로 재설정:** RABBIT_QUEUE=motion.ai.generate.item, routing ai.generate.item(바인딩 확인: exchange motion.ai→base 큐). 소비자 4. (.cpu/.gpu 분리는 v1에서 미사용.) 콜백 /internal/v1/** 은 nginx 프록시 됨(/internal/v2/** 는 아직 없음).
**V2 전환용(저장소 이미 존재):** docs/contracts/alpha-worker-v2.md(콜백 claim/progress/checkpoint/outcome, HMAC, 큐), infra/ai-worker/alpha_v2.py(v2 소비자), infra/rabbitmq/alpha-item-v2-topology.json(큐 motion.alpha.item.v2). AI 쪽 남은 작업=ALPHA_PIPELINE_FACTORY 어댑터(generate/cutout/publish) 연결.
**주의:** MOTION_ALPHA_ENABLED=false 로 내리면 안 됨(주문·결제·주소·상품·네이버 로그인까지 닫힘). 업로드 흐름 검증됨(presigned→PUT→complete, 사진 uploadId 29). Idempotency-Key는 UUID 형식(docs/contracts/alpha-confirmed-fe-api.md).
