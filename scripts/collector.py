#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
청약홈 Open API 수집기
=====================
GitHub Actions(.github/workflows/update-data.yml)에서 주기적으로 실행되어
청약홈 공공데이터를 호출하고, 프런트(index.html)가 기대하는 구조로 가공해
저장소 루트의 data.json 으로 저장합니다. 서버가 필요 없습니다.

구조(실제 API 확인 기준)
  - 경쟁률 API에는 단지명·지역이 없고 주택관리번호(HOUSE_MANAGE_NO)만 있어,
    분양정보 API와 HOUSE_MANAGE_NO 로 조인해 단지명·지역을 붙입니다.
  - 둘 다 최신순(page=1이 최신)이라 양쪽 최신 N건을 받아 교집합으로 가공합니다.

안전장치
  - SERVICE_KEY 가 없거나 호출/가공이 실패하면 data.json 을 건드리지 않고 정상 종료
    (= 마지막 정상 데이터/데모 유지).

가점 컷(seoulCut, cutoff)
  - 신청·당첨자 정보 서비스(15110812)는 별도 활용신청과 정확한 오퍼레이션 경로가 필요합니다.
    CUTOFF_BASE 를 채우면 자동 연동되고, 비어 있으면 cutoff 는 null 로 둡니다(나머지는 정상).
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

# 분양정보 조회 (15098547) — 확인됨
DETAIL_BASE = "https://api.odcloud.kr/api/ApplyhomeInfoDetailSvc/v1/getAPTLttotPblancDetail"
# 청약접수 경쟁률 (15098905) — 확인됨
RATE_BASE = "https://api.odcloud.kr/api/ApplyhomeInfoCmpetRtSvc/v1/getAPTLttotPblancCmpet"

# 당첨 가점 통계 (15110812) — 확인됨. 단, 공고별이 아니라 "지역 × 월" 통계라
# 단지별 cutoff 는 채울 수 없고, 지역(서울) 가점 컷만 산출합니다.
SCORE_BASE = "https://api.odcloud.kr/api/ApplyhomeStatSvc/v1/getAPTApsPrzwnerStat"
SCORE_REGION_FOR_METRIC = "서울"   # metrics.seoulCut 에 쓸 지역명

# 수집 범위(최신 위주) — 변경이 있을 때만 커밋되므로 넉넉히 둬도 부담 없음
DETAIL_PAGES, DETAIL_PER = 3, 100     # 분양정보 최신 ~300 공고
RATE_PAGES, RATE_PER = 6, 1000        # 경쟁률 최신 ~6000 행
# ===========================================================================

KST = timezone(timedelta(hours=9))


def log(msg: str) -> None:
    print(f"[collector] {msg}", flush=True)


def fetch(base: str, page: int, per: int) -> list:
    """odcloud 표준 응답({data:[...]})에서 한 페이지를 받아 리스트로 반환."""
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


def parse_rate(v) -> float:
    """'9.88', '312.5:1', '-', '미달' 등을 float로 정규화. 파싱 불가면 -1."""
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


def area_of(house_ty) -> int | None:
    """'059.9988A' → 59 (전용면적, ㎡)."""
    m = re.match(r"\s*0*(\d{2,3})", str(house_ty))
    return int(m.group(1)) if m else None


def fetch_region_cutoffs() -> dict:
    """지역명 → 최신 월 '해당지역' 당첨 최저가점(LWET_SCORE) 매핑. 실패 시 {}."""
    try:
        rows = fetch_pages(SCORE_BASE, 1, 1000)   # 약 900여 건, 한 페이지로 충분
    except Exception as e:  # noqa: BLE001
        log(f"가점 통계 API 실패: {e} — 가점 컷은 비워 둡니다.")
        return {}
    best = {}   # region -> (stat_de, lwet)
    for r in rows:
        if str(r.get("RESIDE_SECD", "")) != "01":   # 해당지역(실거주권) 기준
            continue
        region = (r.get("SUBSCRPT_AREA_CODE_NM") or "").strip()
        de = str(r.get("STAT_DE", ""))
        lwet = r.get("LWET_SCORE")
        if not region or lwet in (None, ""):
            continue
        try:
            lwet = float(lwet)
        except (ValueError, TypeError):
            continue
        if region not in best or de > best[region][0]:
            best[region] = (de, lwet)
    cuts = {k: round(v[1]) for k, v in best.items()}
    log(f"지역 가점 컷 {len(cuts)}개 (예: 서울={cuts.get('서울')})")
    return cuts


def collect() -> list:
    # 1) 분양정보 → 공고 메타(단지명·지역)
    details = fetch_pages(DETAIL_BASE, DETAIL_PAGES, DETAIL_PER)
    log(f"분양정보 {len(details)}건 수신")
    info = {}
    for d in details:
        no = str(d.get("HOUSE_MANAGE_NO", "")).strip()
        if not no:
            continue
        info[no] = {
            "name": (d.get("HOUSE_NM") or "이름 미상").strip(),
            "region": (d.get("SUBSCRPT_AREA_CODE_NM") or "기타").strip(),
        }

    # 2) 경쟁률 → 공고별 집계(유효 경쟁률, 면적)
    rate_rows = fetch_pages(RATE_BASE, RATE_PAGES, RATE_PER)
    log(f"경쟁률 {len(rate_rows)}행 수신")
    agg = defaultdict(lambda: {"rates": [], "areas": set()})
    for r in rate_rows:
        no = str(r.get("HOUSE_MANAGE_NO", "")).strip()
        if no not in info:           # 분양정보에 있는 최근 공고만
            continue
        cr = parse_rate(r.get("CMPET_RATE"))
        if cr > 0:
            agg[no]["rates"].append(cr)
        a = area_of(r.get("HOUSE_TY"))
        if a:
            agg[no]["areas"].add(a)

    # 3) 조인 → 단지 목록(경쟁률이 확정된 공고만)
    complexes = []
    for no, meta in info.items():
        a = agg.get(no)
        if not a or not a["rates"]:
            continue
        rate = round(sum(a["rates"]) / len(a["rates"]), 1)
        areas = sorted(a["areas"])
        if len(areas) >= 2:
            area_str = f"전용 {areas[0]}~{areas[-1]}㎡"
        elif areas:
            area_str = f"전용 {areas[0]}㎡"
        else:
            area_str = "—"
        complexes.append({
            "name": meta["name"],
            "region": meta["region"],
            "area": area_str,
            "gj": None, "ch": None,        # 가점/추첨 비율: 공공데이터 미제공
            "rate": rate,
            "cutoff": None,                # 단지별 가점 컷: 공공데이터 미제공(지역 통계만 있음)
            "special": [],
        })
    return complexes


def build_payload(complexes: list, region_cuts: dict) -> dict:
    if not complexes:
        return {}
    region_cuts = region_cuts or {}

    by_region = defaultdict(list)
    for c in complexes:
        by_region[c["region"]].append(c["rate"])
    regions = sorted(
        ({"label": k, "rate": round(sum(v) / len(v), 1)} for k, v in by_region.items()),
        key=lambda x: x["rate"], reverse=True,
    )

    buckets = [
        ("미달", "cold", lambda x: x < 1),
        ("1~10 : 1", "calm", lambda x: 1 <= x < 10),
        ("10~50 : 1", "warm", lambda x: 10 <= x < 50),
        ("50~100 : 1", "hot", lambda x: 50 <= x < 100),
        ("100 : 1 이상", "fire", lambda x: x >= 100),
    ]
    dist = [{"label": l, "tier": t, "n": sum(1 for c in complexes if p(c["rate"]))}
            for l, t, p in buckets]

    seoul = [c["rate"] for c in complexes if c["region"] == "서울"]
    allr = [c["rate"] for c in complexes]
    shortfall = sum(1 for c in complexes if c["rate"] < 1)
    seoul_cut = region_cuts.get(SCORE_REGION_FOR_METRIC)   # 서울 지역 최신 당첨 최저가점

    metrics = {
        "seoulAvg": round(sum(seoul) / len(seoul), 1) if seoul else None,
        "nationAvg": round(sum(allr) / len(allr), 1) if allr else None,
        "shortfall": shortfall,
        "seoulCut": seoul_cut,
    }

    complexes.sort(key=lambda c: c["rate"], reverse=True)   # 화면 보기 좋게 경쟁률 내림차순

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
        log("SERVICE_KEY 미설정 — data.json 을 건드리지 않고 종료합니다(데모/기존 유지).")
        return 0
    try:
        complexes = collect()
        region_cuts = fetch_region_cutoffs()   # 실패해도 {} → 가점 컷만 비고 나머지 정상
    except Exception as e:  # noqa: BLE001
        log(f"수집 실패: {e} — data.json 유지하고 종료.")
        return 0
    payload = build_payload(complexes, region_cuts)
    if not payload:
        log("가공 결과가 비어 있음(최근 공고 중 경쟁률 확정분 없음) — data.json 유지하고 종료.")
        return 0

    out = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data.json"))
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    log(f"data.json 작성 완료 — 단지 {len(payload['complexes'])}건, 수집시각 {payload['collectedAt']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
