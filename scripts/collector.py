#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
청약홈 Open API 수집기 (3단계 도구용)
====================================
GitHub Actions(.github/workflows/update-data.yml)에서 주기적으로 실행되어
청약홈 공공데이터를 호출하고, 프런트(index.html)가 기대하는 구조로 가공해
저장소 루트의 data.json 으로 저장합니다. 서버가 필요 없습니다.

이 도구가 쓰는 데이터(딱 두 갈래)
  1) 분양정보  getAPTLttotPblancDetail        : 현재 모집 중인 '아파트' 공고
     + getAPTLttotPblancMdl                   : 공고별 전용면적(주택형)
     + getUrbtyOfctlLttotPblancDetail         : 오피스텔/도시형/생숙/민간임대 공고
     → "이번 주 분양 목록" + 유형 태그(가점 적용 여부)
  2) 당첨 통계 getAPTApsPrzwnerStat            : 지역×월 당첨 가점 통계(평균/최저/최고)
     → "이 지역은 최근 보통 ○○점대에서 당첨됐다" 밴드 (3단계 비교용)

안전장치
  - SERVICE_KEY 가 없으면 data.json 을 건드리지 않고 종료(= 마지막 데모 유지).
  - 보조 호출(면적/오피스텔/통계)이 실패해도 그 항목만 비고 나머지는 정상 생성.
"""

import json
import os
import re
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone, timedelta

# ===== 설정 ================================================================
SERVICE_KEY = os.environ.get("SERVICE_KEY", "").strip()

DETAIL_BASE = "https://api.odcloud.kr/api/ApplyhomeInfoDetailSvc/v1/getAPTLttotPblancDetail"
MODEL_BASE = "https://api.odcloud.kr/api/ApplyhomeInfoDetailSvc/v1/getAPTLttotPblancMdl"
OFTL_BASE = "https://api.odcloud.kr/api/ApplyhomeInfoDetailSvc/v1/getUrbtyOfctlLttotPblancDetail"
# 지역×월 당첨 가점 통계(지역별 평균/최저/최고)
REGION_STAT_BASE = "https://api.odcloud.kr/api/ApplyhomeStatSvc/v1/getAPTApsPrzwnerStat"

DETAIL_PAGES, DETAIL_PER = 3, 100     # 분양정보 최신 ~300 공고
MODEL_PAGES, MODEL_PER = 6, 1000      # 주택형(면적) 최신 ~6000 행
OFTL_PAGES, OFTL_PER = 2, 100         # 오피스텔 등 최신 ~200 공고
# ===========================================================================

KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST).date().isoformat()


def log(msg: str) -> None:
    print(f"[collector] {msg}", flush=True)


def fetch(base: str, page: int, per: int) -> list:
    qs = urllib.parse.urlencode({"page": page, "perPage": per, "serviceKey": SERVICE_KEY})
    req = urllib.request.Request(f"{base}?{qs}", headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return payload["data"]
    return []


def fetch_pages(base: str, pages: int, per: int) -> list:
    out = []
    for p in range(1, pages + 1):
        rows = fetch(base, p, per)
        if not rows:
            break
        out.extend(rows)
    return out


def to_float(v):
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError, AttributeError):
        return None


def pick(d: dict, *keys) -> str:
    """후보 키들 중 처음으로 값이 있는 것을 문자열로. 없으면 ''."""
    for k in keys:
        v = d.get(k)
        if v not in (None, "", " "):
            return str(v).strip()
    return ""


def area_of(house_ty):
    """'059.9988A' → 59 (전용면적, ㎡)."""
    m = re.match(r"\s*0*(\d{2,3})", str(house_ty))
    return int(m.group(1)) if m else None


def area_str(areas: set) -> str:
    a = sorted(x for x in areas if x)
    if len(a) >= 2:
        return f"전용 {a[0]}~{a[-1]}㎡"
    if a:
        return f"전용 {a[0]}㎡"
    return "—"


# ===== 1) 분양 목록 ========================================================

def classify_apt(d: dict):
    """(유형 라벨, gajeom 여부) — 가점제는 민영 APT 일반공급에만 적용."""
    secd = pick(d, "HOUSE_SECD_NM")
    dtl = pick(d, "HOUSE_DTL_SECD_NM")
    rent = pick(d, "RENT_SECD_NM")
    if "임대" in rent or "임대" in dtl:
        return "임대주택", False
    if "민영" in secd or "민영" in dtl:
        return "민영 일반공급 (가점제)", True
    if "국민" in secd or "공공" in dtl or "국민" in dtl or "신혼희망" in dtl:
        return "국민·공공 분양", False
    return dtl or "아파트", False


def schedule_of(d: dict, is_apt: bool) -> dict:
    notice = pick(d, "RCRIT_PBLANC_DE")
    special = pick(d, "SPSPLY_RCEPT_BGNDE")
    result = pick(d, "PRZWNER_PRESNATN_DE")
    if is_apt:
        rank1 = pick(d, "GNRL_RNK1_CRSPAREA_RCPTDE", "SUBSCRPT_RCEPT_BGNDE")
    else:
        rank1 = pick(d, "SUBSCRPT_RCEPT_BGNDE")
    return {"notice": notice, "special": special, "rank1": rank1, "result": result}


def latest_date(d: dict) -> str:
    """공고의 모든 일정 필드 중 가장 늦은 날짜(진행 여부 판정용)."""
    keys = [
        "SUBSCRPT_RCEPT_ENDDE", "SPSPLY_RCEPT_ENDDE", "PRZWNER_PRESNATN_DE",
        "GNRL_RNK1_CRSPAREA_ENDDE", "GNRL_RNK1_ETC_AREA_ENDDE",
        "GNRL_RNK2_CRSPAREA_ENDDE", "GNRL_RNK2_ETC_AREA_ENDDE",
        "CNTRCT_CNCLS_ENDDE",
    ]
    vals = [pick(d, k) for k in keys]
    vals = [v for v in vals if re.match(r"\d{4}-\d{2}-\d{2}", v)]
    return max(vals) if vals else ""


def fetch_model_areas() -> dict:
    """주택관리번호 → 전용면적 집합. 실패 시 {}."""
    try:
        rows = fetch_pages(MODEL_BASE, MODEL_PAGES, MODEL_PER)
    except Exception as e:  # noqa: BLE001
        log(f"주택형(면적) API 실패: {e} — 면적 비움.")
        return {}
    areas = defaultdict(set)
    for r in rows:
        no = pick(r, "HOUSE_MANAGE_NO")
        a = area_of(pick(r, "HOUSE_TY"))
        if no and a:
            areas[no].add(a)
    log(f"면적 {len(areas)}개 공고")
    return areas


def collect_notices() -> list:
    notices = []
    areas = fetch_model_areas()

    # 1) 아파트 분양정보
    details = fetch_pages(DETAIL_BASE, DETAIL_PAGES, DETAIL_PER)
    log(f"아파트 분양정보 {len(details)}건 수신")
    for d in details:
        no = pick(d, "HOUSE_MANAGE_NO")
        if not no:
            continue
        if latest_date(d) and latest_date(d) < TODAY:   # 이미 끝난 공고 제외
            continue
        label, gajeom = classify_apt(d)
        notices.append({
            "name": pick(d, "HOUSE_NM") or "이름 미상",
            "region": pick(d, "SUBSCRPT_AREA_CODE_NM") or "기타",
            "area": area_str(areas.get(no, set())),
            "type": label,
            "gajeom": gajeom,
            "hasSpecial": bool(pick(d, "SPSPLY_RCEPT_BGNDE")),
            "schedule": schedule_of(d, is_apt=True),
            "url": pick(d, "PBLANC_URL", "HMPG_ADRES"),
        })

    # 2) 오피스텔/도시형/생숙/민간임대 (가점제 아님)
    try:
        ofts = fetch_pages(OFTL_BASE, OFTL_PAGES, OFTL_PER)
        log(f"오피스텔 등 {len(ofts)}건 수신")
        for d in ofts:
            no = pick(d, "HOUSE_MANAGE_NO")
            if not no:
                continue
            if latest_date(d) and latest_date(d) < TODAY:
                continue
            notices.append({
                "name": pick(d, "HOUSE_NM") or "이름 미상",
                "region": pick(d, "SUBSCRPT_AREA_CODE_NM") or "기타",
                "area": area_str(areas.get(no, set())),
                "type": pick(d, "HOUSE_DTL_SECD_NM") or "오피스텔·도시형",
                "gajeom": False,
                "hasSpecial": bool(pick(d, "SPSPLY_RCEPT_BGNDE")),
                "schedule": schedule_of(d, is_apt=False),
                "url": pick(d, "PBLANC_URL", "HMPG_ADRES"),
            })
    except Exception as e:  # noqa: BLE001
        log(f"오피스텔 API 실패: {e} — 아파트만 사용.")

    # 빠른 일정(1순위/접수 시작) 순으로 정렬
    notices.sort(key=lambda n: n["schedule"].get("rank1") or n["schedule"].get("notice") or "9999")
    return notices


# ===== 2) 지역 당첨 가점 밴드 ==============================================

def fetch_region_scores() -> dict:
    """지역명 → {month, avg, low, top} 최신월 '해당지역' 당첨 가점 통계. 실패 시 {}."""
    try:
        rows = fetch_pages(REGION_STAT_BASE, 1, 1000)
    except Exception as e:  # noqa: BLE001
        log(f"지역 당첨 통계 실패: {e} — regionScores 비움.")
        return {}
    best = {}   # region -> (month, avg, low, top)
    for r in rows:
        if str(r.get("RESIDE_SECD", "")) not in ("01", ""):   # 해당지역(실거주권) 기준
            continue
        region = pick(r, "SUBSCRPT_AREA_CODE_NM")
        de = pick(r, "STAT_DE")
        avg = to_float(r.get("AVRG_SCORE"))
        if not region or avg is None or avg <= 0:
            continue
        if region not in best or de > best[region][0]:
            best[region] = (de, avg, to_float(r.get("LWET_SCORE")), to_float(r.get("TOP_SCORE")))
    out = {}
    for k, (de, avg, low, top) in best.items():
        out[k] = {
            "month": f"{de[:4]}.{de[4:6]}" if len(de) >= 6 else de,
            "avg": round(avg, 1),
            "low": round(low) if low else None,
            "top": round(top) if top else None,
        }
    log(f"지역 당첨 통계 {len(out)}개 지역")
    return out


# ===== 조립 ================================================================

def main() -> int:
    if not SERVICE_KEY:
        log("SERVICE_KEY 미설정 — data.json 을 건드리지 않고 종료합니다(데모 유지).")
        return 0
    try:
        notices = collect_notices()
        region_scores = fetch_region_scores()
    except Exception as e:  # noqa: BLE001
        log(f"수집 실패: {e} — data.json 유지하고 종료.")
        return 0
    if not notices:
        log("모집 중 공고가 비어 있음 — data.json 유지하고 종료.")
        return 0

    payload = {
        "source": "github-actions",
        "live": True,
        "collectedAt": datetime.now(KST).isoformat(timespec="seconds"),
        "notices": notices,
        "regionScores": region_scores,
    }
    out = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data.json"))
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    log(f"data.json 작성 완료 — 공고 {len(notices)}건, 수집시각 {payload['collectedAt']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
