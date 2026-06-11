#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
청약홈 Open API 수집기
=====================
GitHub Actions(.github/workflows/update-data.yml)에서 주기적으로 실행되어
청약홈 공공데이터를 호출하고, 프런트(index.html)가 기대하는 구조로 가공해
저장소 루트의 data.json 으로 저장합니다. 서버가 필요 없습니다.

안전장치
  - 환경변수 SERVICE_KEY(공공데이터포털 인증키)가 없으면
    data.json 을 건드리지 않고 그대로 정상 종료합니다(데모/기존 데이터 유지).
  - API 호출이 실패하거나 가공 결과가 비어 있으면 역시 data.json 을 쓰지 않습니다.
    → 한 번 받아둔 마지막 정상 데이터가 덮어써지지 않습니다.

⚠️ 오퍼레이션 이름과 응답 필드명은 공공데이터포털 Swagger에서 한 번 확인하세요.
   다르면 아래 "===== 설정 =====" 구간의 상수만 고치면 됩니다.
   - 분양정보  : https://www.data.go.kr/data/15098547/openapi.do
   - 경쟁률    : https://www.data.go.kr/data/15098905/openapi.do
   - 신청·당첨 : https://www.data.go.kr/data/15110812/openapi.do  (가점 컷 연동용, 추후)
"""

import json
import os
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone, timedelta

# ===== 설정 (Swagger 확인 후 필요 시 이 부분만 수정) =========================
SERVICE_KEY = os.environ.get("SERVICE_KEY", "").strip()

# 분양정보 조회 서비스 (15098547)
BUNYANG_BASE = "https://api.odcloud.kr/api/ApplyhomeInfoDetailSvc/v1/getAPTLttotPblancDetail"
# 경쟁률 조회 서비스 (15098905)
RATE_BASE = "https://api.odcloud.kr/api/ApplyhomeInfoCmpetRtSvc/v1/getAPTLttotPblancCmpet"

# 한 번에 받아올 공고 수 (최근분 위주)
PAGE_SIZE = 100

# 청약 신청·당첨자 정보 — 당첨 가점 (15110812)
# ⚠️ 오퍼레이션/필드명은 추정값입니다. 키 발급 후 Swagger에서 한 번 확인하세요.
CUTOFF_BASE = "https://api.odcloud.kr/api/ApplyhomeInfoPlfaSvc/v1/getAPTLttotPblancMdat"

# 응답 필드명(Swagger 기준 추정값) — 다르면 여기만 교체
F_NAME = "HOUSE_NM"        # 단지(주택)명
F_REGION = "SUBSCRPT_AREA_CODE_NM"  # 공급지역명 (예: 서울, 경기 …)
F_AREA = "HOUSE_DTL_SECD_NM"        # 주택형/면적 구분명
F_RATE = "CMPET_RATE"      # 경쟁률(숫자 또는 "n.n:1" 형태)
F_PUBLISH = "RCRIT_PBLANC_DE"       # 모집공고일(YYYYMMDD)
F_HOUSE_NO = "HOUSE_MANAGE_NO"      # 주택관리번호(경쟁률·가점을 잇는 매칭 키)

# 가점 응답 필드(추정값)
FC_HOUSE_NO = "HOUSE_MANAGE_NO"     # 주택관리번호(매칭 키)
FC_SCORE = "GNRL_LWET_POINT"        # 일반공급 당첨 '최저가점'
# ===========================================================================

KST = timezone(timedelta(hours=9))


def log(msg: str) -> None:
    print(f"[collector] {msg}", flush=True)


def fetch(base: str, page: int = 1) -> list:
    """odcloud 표준 응답({data:[...]})을 가정하고 한 페이지를 받아 리스트로 반환."""
    qs = urllib.parse.urlencode({
        "page": page,
        "perPage": PAGE_SIZE,
        "serviceKey": SERVICE_KEY,
    })
    url = f"{base}?{qs}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    # odcloud: {"data":[...]} / 일부 서비스: {"response":{"body":{"items":[...]}}}
    if isinstance(payload, dict):
        if isinstance(payload.get("data"), list):
            return payload["data"]
        body = payload.get("response", {}).get("body", {})
        items = body.get("items")
        if isinstance(items, dict):
            items = items.get("item", [])
        if isinstance(items, list):
            return items
    return []


def parse_rate(v) -> float:
    """'312.5:1', '△', '미달', 312.5 등 다양한 표기를 float로 정규화. 실패 시 -1."""
    if v is None:
        return -1.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if s in ("", "-", "△", "미달", "접수없음"):
        return 0.0
    s = s.replace(":1", "").replace(":", "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return -1.0


def region_short(name: str) -> str:
    """'서울특별시' → '서울' 처럼 짧은 라벨로."""
    if not name:
        return "기타"
    name = str(name)
    table = [
        ("서울", "서울"), ("경기", "경기"), ("인천", "인천"), ("부산", "부산"),
        ("대구", "대구"), ("대전", "대전"), ("광주", "광주"), ("울산", "울산"),
        ("세종", "세종"), ("강원", "강원"), ("충북", "충북"), ("충남", "충남"),
        ("전북", "전북"), ("전남", "전남"), ("경북", "경북"), ("경남", "경남"),
        ("제주", "제주"),
    ]
    for key, short in table:
        if name.startswith(key):
            return short
    return name


def tier_for(rate: float) -> str:
    if rate < 1:
        return "cold"
    if rate < 10:
        return "calm"
    if rate < 50:
        return "warm"
    if rate < 100:
        return "hot"
    return "fire"


def fetch_cutoffs() -> dict:
    """주택관리번호 → 당첨 최저가점(float) 매핑을 반환. 실패하면 빈 dict(= 가점 컷 비움)."""
    try:
        rows = fetch(CUTOFF_BASE)
    except Exception as e:  # noqa: BLE001
        log(f"가점 API 호출 실패: {e} — 가점 컷은 비워 둡니다(나머지는 정상).")
        return {}
    cuts = {}
    for r in rows:
        no = str(r.get(FC_HOUSE_NO, "")).strip()
        raw = r.get(FC_SCORE)
        if not no or raw in (None, ""):
            continue
        try:
            s = float(str(raw).replace(",", "").strip())
        except (ValueError, TypeError):
            continue
        # 한 공고에 주택형이 여러 개면 최저가점들 중 가장 낮은 값을 그 공고의 컷으로
        cuts[no] = min(cuts.get(no, s), s)
    log(f"가점 정보 {len(cuts)}개 공고 매핑")
    return cuts


def build_payload(rate_rows: list, cutoffs: dict) -> dict:
    """경쟁률 응답 + 가점 매핑을 index.html 의 DATA 구조로 가공."""
    cutoffs = cutoffs or {}
    complexes = []
    for r in rate_rows:
        rate = parse_rate(r.get(F_RATE))
        if rate < 0:
            continue
        house_no = str(r.get(F_HOUSE_NO, "")).strip()
        cut = cutoffs.get(house_no)
        complexes.append({
            "name": str(r.get(F_NAME, "")).strip() or "이름 미상",
            "region": region_short(r.get(F_REGION, "")),
            "area": str(r.get(F_AREA, "")).strip() or "—",
            "gj": None, "ch": None,            # 가점/추첨 비율: 분양정보 연동 후 보강
            "rate": round(rate, 1),
            "cutoff": round(cut) if isinstance(cut, (int, float)) else None,
            "special": [],
        })

    if not complexes:
        return {}

    # 지역별 평균 경쟁률
    by_region = defaultdict(list)
    for c in complexes:
        by_region[c["region"]].append(c["rate"])
    regions = sorted(
        ({"label": k, "rate": round(sum(v) / len(v), 1)} for k, v in by_region.items()),
        key=lambda x: x["rate"], reverse=True,
    )

    # 경쟁률 분포 버킷
    buckets = [
        ("미달", "cold", lambda x: x < 1),
        ("1~10 : 1", "calm", lambda x: 1 <= x < 10),
        ("10~50 : 1", "warm", lambda x: 10 <= x < 50),
        ("50~100 : 1", "hot", lambda x: 50 <= x < 100),
        ("100 : 1 이상", "fire", lambda x: x >= 100),
    ]
    dist = []
    for label, tier, pred in buckets:
        n = sum(1 for c in complexes if pred(c["rate"]))
        dist.append({"label": label, "n": n, "tier": tier})

    seoul_rates = [c["rate"] for c in complexes if c["region"] == "서울"]
    all_rates = [c["rate"] for c in complexes]
    shortfall = sum(1 for c in complexes if c["rate"] < 1)

    # 서울 당첨 가점 컷 = 서울 단지들의 당첨 최저가점 평균(가점 데이터 있을 때만)
    seoul_cuts = [c["cutoff"] for c in complexes if c["region"] == "서울" and c["cutoff"] is not None]
    seoul_cut = round(sum(seoul_cuts) / len(seoul_cuts)) if seoul_cuts else None

    metrics = {
        "seoulAvg": round(sum(seoul_rates) / len(seoul_rates), 1) if seoul_rates else None,
        "nationAvg": round(sum(all_rates) / len(all_rates), 1) if all_rates else None,
        "shortfall": shortfall,
        "seoulCut": seoul_cut,   # 당첨 가점 컷(가점 API 연동 — 데이터 없으면 null)
    }

    return {
        "source": "github-actions",
        "live": True,
        "collectedAt": datetime.now(KST).isoformat(timespec="seconds"),
        "asOf": "",
        "metrics": metrics,
        "refs": {"lotteryCase": shortfall, "seoulCut": seoul_cut, "gangnamMin": None},
        "regions": regions,
        "dist": dist,
        "complexes": complexes,
    }


def main() -> int:
    if not SERVICE_KEY:
        log("SERVICE_KEY 미설정 — data.json 을 건드리지 않고 종료합니다(데모 유지).")
        return 0

    try:
        rate_rows = fetch(RATE_BASE)
    except Exception as e:  # noqa: BLE001 — 네트워크/파싱 모든 실패를 안전 종료로
        log(f"API 호출 실패: {e} — data.json 유지하고 종료.")
        return 0

    log(f"경쟁률 응답 {len(rate_rows)}건 수신")
    cutoffs = fetch_cutoffs()   # 실패해도 {} → 가점 컷만 비고 나머지는 정상
    payload = build_payload(rate_rows, cutoffs)
    if not payload:
        log("가공 결과가 비어 있음(필드명 불일치 가능) — data.json 유지하고 종료.")
        log("→ Swagger에서 응답 필드명을 확인하고 collector.py 상단 상수를 맞춰 주세요.")
        return 0

    out = os.path.join(os.path.dirname(__file__), "..", "data.json")
    out = os.path.abspath(out)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    log(f"data.json 작성 완료 — 단지 {len(payload['complexes'])}건, 수집시각 {payload['collectedAt']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
