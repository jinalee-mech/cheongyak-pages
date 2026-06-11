#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
청약홈 Open API 수집기
=====================
GitHub Actions(.github/workflows/update-data.yml)에서 주기적으로 실행되어
청약홈 공공데이터를 호출하고, 프런트(index.html)가 기대하는 구조로 가공해
저장소 루트의 data.json 으로 저장합니다. 서버가 필요 없습니다.

조립 구조(실제 API 확인 기준) — 모두 HOUSE_MANAGE_NO(주택관리번호)로 조인
  1) 분양정보  getAPTLttotPblancDetail  : 단지명·지역
  2) 경쟁률    getAPTLttotPblancCmpet   : 단지 평균 경쟁률(단지명/지역 없음 → 1과 조인)
  3) 당첨가점  getAptLttotPblancScore   : 단지별 당첨 최저가점(cutoff)
  4) 특별공급  getAPTSpsplyReqstStus    : 특별공급 유형(special)

안전장치
  - SERVICE_KEY 가 없거나 호출/가공이 실패하면 data.json 을 건드리지 않고 정상 종료
    (= 마지막 정상 데이터/데모 유지). 보조 API(가점·특공)가 실패해도 그 항목만 비고
    나머지는 정상 생성합니다.

남는 한계
  - 가점제/추첨제 비율(gj/ch)은 공공데이터 미제공 → null.
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
RATE_BASE = "https://api.odcloud.kr/api/ApplyhomeInfoCmpetRtSvc/v1/getAPTLttotPblancCmpet"
SCORE_BASE = "https://api.odcloud.kr/api/ApplyhomeInfoCmpetRtSvc/v1/getAptLttotPblancScore"
SPSPLY_BASE = "https://api.odcloud.kr/api/ApplyhomeInfoCmpetRtSvc/v1/getAPTSpsplyReqstStus"

# 특별공급 유형 매핑(세대수 필드 → 표시명). 한국부동산원 표준 약어 기준.
SPECIAL_MAP = [
    ("NWWDS_NMTW_HSHLDCO", "신혼부부"),
    ("LFE_FRST_HSHLDCO", "생애최초"),
    ("MNYCH_HSHLDCO", "다자녀"),
    ("OLD_PARNTS_SUPORT_HSHLDCO", "노부모"),
    ("NWBB_NWBBSHR_HSHLDCO", "신생아"),
    ("INSTT_RECOMEND_HSHLDCO", "기관추천"),
]
SPECIAL_ORDER = [label for _, label in SPECIAL_MAP]

# 수집 범위(최신 위주) — 변경 시에만 커밋되므로 넉넉히 둬도 부담 없음
DETAIL_PAGES, DETAIL_PER = 3, 100     # 분양정보 최신 ~300 공고
RATE_PAGES, RATE_PER = 6, 1000        # 경쟁률 최신 ~6000 행
SCORE_PAGES, SCORE_PER = 6, 1000      # 가점 최신 ~6000 행
SPSPLY_PAGES, SPSPLY_PER = 3, 1000    # 특공 최신 ~3000 행
# ===========================================================================

KST = timezone(timedelta(hours=9))


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


def parse_rate(v) -> float:
    """'9.88', '312.5:1', '-', '미달' 등을 float로. 파싱 불가면 -1."""
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


def area_of(house_ty):
    """'059.9988A' → 59 (전용면적, ㎡)."""
    m = re.match(r"\s*0*(\d{2,3})", str(house_ty))
    return int(m.group(1)) if m else None


def fetch_house_cutoffs() -> dict:
    """주택관리번호 → 해당지역 당첨 '최저가점'(단지 진입선). 실패 시 {}."""
    try:
        rows = fetch_pages(SCORE_BASE, SCORE_PAGES, SCORE_PER)
    except Exception as e:  # noqa: BLE001
        log(f"가점 API 실패: {e} — cutoff 비움(나머지 정상).")
        return {}
    cuts = {}
    for r in rows:
        if str(r.get("RESIDE_SECD", "")) != "01":   # 해당지역(실거주권) 기준
            continue
        no = str(r.get("HOUSE_MANAGE_NO", "")).strip()
        lw = to_float(r.get("LWET_SCORE"))
        if not no or lw is None or lw <= 0:
            continue
        cuts[no] = min(cuts.get(no, lw), lw)        # 단지 내 가장 낮은 당첨가점 = 진입선
    log(f"가점 컷 {len(cuts)}개 공고")
    return {k: round(v) for k, v in cuts.items()}


def fetch_specials() -> dict:
    """주택관리번호 → 특별공급 유형 리스트. 실패 시 {}."""
    try:
        rows = fetch_pages(SPSPLY_BASE, SPSPLY_PAGES, SPSPLY_PER)
    except Exception as e:  # noqa: BLE001
        log(f"특공 API 실패: {e} — special 비움(나머지 정상).")
        return {}
    sp = defaultdict(set)
    for r in rows:
        no = str(r.get("HOUSE_MANAGE_NO", "")).strip()
        if not no:
            continue
        for field, label in SPECIAL_MAP:
            n = to_float(r.get(field)) or 0
            if n > 0:
                sp[no].add(label)
    log(f"특별공급 {len(sp)}개 공고")
    return {k: [l for l in SPECIAL_ORDER if l in v] for k, v in sp.items()}


def collect() -> list:
    # 1) 분양정보 → 공고 메타
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

    # 2) 경쟁률 → 공고별 집계
    rate_rows = fetch_pages(RATE_BASE, RATE_PAGES, RATE_PER)
    log(f"경쟁률 {len(rate_rows)}행 수신")
    agg = defaultdict(lambda: {"rates": [], "areas": set()})
    for r in rate_rows:
        no = str(r.get("HOUSE_MANAGE_NO", "")).strip()
        if no not in info:
            continue
        cr = parse_rate(r.get("CMPET_RATE"))
        if cr > 0:
            agg[no]["rates"].append(cr)
        a = area_of(r.get("HOUSE_TY"))
        if a:
            agg[no]["areas"].add(a)

    # 3) 가점 컷, 4) 특별공급 유형
    cutoffs = fetch_house_cutoffs()
    specials = fetch_specials()

    # 5) 조인 → 단지 목록(경쟁률이 확정된 공고만)
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
            "gj": None, "ch": None,               # 가점제/추첨제 비율: 공공데이터 미제공
            "rate": rate,
            "cutoff": cutoffs.get(no),            # 단지별 당첨 최저가점(없으면 None)
            "special": specials.get(no, []),
        })
    return complexes


def build_payload(complexes: list) -> dict:
    if not complexes:
        return {}

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
    seoul_cuts = [c["cutoff"] for c in complexes if c["region"] == "서울" and c["cutoff"] is not None]
    seoul_cut = round(sum(seoul_cuts) / len(seoul_cuts)) if seoul_cuts else None

    metrics = {
        "seoulAvg": round(sum(seoul) / len(seoul), 1) if seoul else None,
        "nationAvg": round(sum(allr) / len(allr), 1) if allr else None,
        "shortfall": shortfall,
        "seoulCut": seoul_cut,
    }

    complexes.sort(key=lambda c: c["rate"], reverse=True)   # 경쟁률 내림차순

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
    except Exception as e:  # noqa: BLE001
        log(f"수집 실패: {e} — data.json 유지하고 종료.")
        return 0
    payload = build_payload(complexes)
    if not payload:
        log("가공 결과가 비어 있음 — data.json 유지하고 종료.")
        return 0

    out = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data.json"))
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    log(f"data.json 작성 완료 — 단지 {len(payload['complexes'])}건, 수집시각 {payload['collectedAt']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
