# 청약 나침반

공공데이터 기반 AI 청약시장 분석 · 맞춤 지원전략 — 기말 프로젝트 데모

라이브 페이지: https://깃허브아이디.github.io/저장소이름/  ← Pages 설정 후 본인 주소로 수정

## 데이터를 찾는 순서 (3단계)

페이지는 켜질 때 아래 순서로 데이터를 찾아 자동 전환됩니다.

1. **FastAPI 백엔드(실시간)** — `/api/market` 이 응답하면 사용. 서비스키까지 있으면 "실시간" 배지.
2. **저장소 `data.json`(자동 갱신)** — 서버가 없을 때 사용. GitHub Actions가 청약홈 API를
   주기적으로 호출해 커밋해 둔 파일로, 초록색 "실데이터 · 자동 갱신" 배지와 수집 시각을 표시.
3. **데모 모드** — 위 둘 다 없으면 가상 샘플 단지 10곳으로 동작(모든 단지명·수치는 가상).

→ **서버 없이 무료로 "API 가져오기"가 되는 구조**입니다. 잠들거나 콜드 스타트가 생길 서버가 없습니다.

## 배포 (GitHub Pages)

이 폴더를 GitHub 저장소에 올리고 **Settings → Pages → Branch: `main` / `/ (root)`** 로 설정.
여기까지만 하면 데모 모드 링크가 됩니다.

## 실데이터 켜기 (키 발급 후 2단계)

1. 공공데이터포털에서 인증키를 발급받아, 저장소
   **Settings → Secrets and variables → Actions** 에서 이름 `SERVICE_KEY` 로 등록.
   (공개 저장소여도 Secret은 노출되지 않습니다.)
2. **Actions 탭 → `update-data` 워크플로 → Run workflow** 로 한 번 수동 실행.
   성공하면 `data.json` 이 자동 커밋되고, 1~2분 뒤 페이지가 초록 배지로 전환됩니다.

이후로는 **매시 정각마다(준실시간)** 자동 갱신됩니다. 변경이 있을 때만 커밋하므로 부담이 없습니다.
주기를 바꾸려면 [.github/workflows/update-data.yml](.github/workflows/update-data.yml) 의 `cron` 한 줄만
수정하세요. (6시간: `"0 */6 * * *"` · 하루 1회 06:00 KST: `"0 21 * * *"`)

## 수집 구조 (실제 API 확인 기준)

[scripts/collector.py](scripts/collector.py) 가 네 API를 모두 `HOUSE_MANAGE_NO`(주택관리번호)로 조인합니다.

1. **분양정보**(`ApplyhomeInfoDetailSvc/getAPTLttotPblancDetail`) — 단지명·지역
2. **경쟁률**(`ApplyhomeInfoCmpetRtSvc/getAPTLttotPblancCmpet`) — 단지 평균 경쟁률(단지명/지역 없어 1과 조인)
3. **당첨 가점**(`ApplyhomeInfoCmpetRtSvc/getAptLttotPblancScore`) — 단지별 당첨 최저가점(`cutoff`)
4. **특별공급**(`ApplyhomeInfoCmpetRtSvc/getAPTSpsplyReqstStus`) — 특별공급 유형(`special`)

가점·특공 API가 실패하면 그 항목만 비고 나머지는 정상 생성됩니다.

## 한계 (정직하게)

- "실시간"이 아니라 **주기 갱신**이지만, 매시 갱신이라 실질 차이는 거의 없고 헤더의 **수집 시각**이
  기준을 정확히 보여줍니다.
- 브라우저에 API 키를 둘 수 없는 구조라 **맞춤 안내문은 규칙 기반**으로 유지됩니다
  (Claude API 연결은 FastAPI 백엔드, 또는 키를 숨길 무료 서버리스 함수 추가 시에만).
- **가점제/추첨제 비율(gj/ch)** 만 공공데이터 미제공이라 `null` 입니다. (단지별 가점 컷·특별공급
  유형은 실데이터로 채워집니다 — 추첨제 단지 등 가점이 없는 곳만 `cutoff`가 `null`.)

## 실데이터 출처

한국부동산원 청약홈 — 공공데이터포털 Open API
- 분양정보 조회 서비스 (15098547)
- 청약접수 경쟁률 및 특별공급 신청현황 조회 서비스 (15098905)
- 청약 신청·당첨자 정보 조회 서비스 (15110812) — 가점 컷 연동용(추후)
