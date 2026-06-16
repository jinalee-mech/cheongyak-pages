#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
청약홈 Open API 수집기 — 자격(순위/특공) + 카테고리 분류용
=========================================================
GitHub Actions에서 주기 실행되어 청약홈 공공데이터를 호출하고, 프런트(index.html)가
기대하는 구조로 가공해 data.json 으로 저장합니다. 서버가 필요 없습니다.

수집 대상(현재 모집 중·예정 공고를 카테고리별로)
  1) APT 분양        getAPTLttotPblancDetail   : 민영/국민/신혼희망 (특공·1·2순위 단계)
       + getAPTLttotPblancMdl                  : 전용면적(주택형)
  2) 무순위/잔여     getRemndrLttotPblancDetail : 무순위 / 불법행위 재공급
  3) 임의공급        getOPTLttotPblancDetail    : 임의공급
  4) 오피스텔 등     getUrbtyOfctlLttotPblancDetail (+Mdl) : 오피스텔/도시형/생숙/민간임대
  5) 공공지원민간임대 getPblPvtRentLttotPblancDetail
  6) 당첨 통계       getAPTApsPrzwnerStat       : 지역별 과거 당첨 가점 밴드(가점 비교용)

프런트가 자격을 계산할 수 있도록 공고마다 region/areaMin/regulated/metro/aptKind/hasSpecial 을 같이 내보냅니다.
SERVICE_KEY 가 없으면 data.json 을 건드리지 않고 종료(데모 유지). 보조 호출 실패는 해당 항목만 비움.
"""

import json
import os
import re
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone, timedelta

SERVICE_KEY = os.environ.get("SERVICE_KEY", "").strip()
BASE = "https://api.odcloud.kr/api/ApplyhomeInfoDetailSvc/v1/"
STAT_BASE = "https://api.odcloud.kr/api/ApplyhomeStatSvc/v1/getAPTApsPrzwnerStat"

KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST).date().isoformat()
METRO = {"서울", "경기", "인천"}


def log(msg): print(f"[collector] {msg}", flush=True)


def fetch_pages(name_or_url, pages, per, is_url=False):
    out = []
    base = name_or_url if is_url else (BASE + name_or_url)
    for p in range(1, pages + 1):
        qs = urllib.parse.urlencode({"page": p, "perPage": per, "serviceKey": SERVICE_KEY})
        req = urllib.request.Request(f"{base}?{qs}", headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not rows:
            break
        out.extend(rows)
    return out


def pick(d, *keys):
    for k in keys:
        v = d.get(k)
        if v not in (None, "", " "):
            return str(v).strip()
    return ""


def to_int_area(ty):
    m = re.match(r"\s*0*(\d{2,3})", str(ty))
    return int(m.group(1)) if m else None


def area_str(areas):
    a = sorted(x for x in areas if x)
    if len(a) >= 2: return f"전용 {a[0]}~{a[-1]}㎡"
    if a: return f"전용 {a[0]}㎡"
    return "—"


def _dates(d, *keys):
    return [v for v in (pick(d, k) for k in keys) if re.match(r"\d{4}-\d{2}-\d{2}", v)]


def is_open(d):
    """신청자 관점 진행 중(발표 전이거나 접수 안 끝남). 날짜 없으면 보수적으로 제외."""
    vals = _dates(d, "PRZWNER_PRESNATN_DE", "RCEPT_ENDDE", "SUBSCRPT_RCEPT_ENDDE",
                  "GNRL_RCEPT_ENDDE", "SPSPLY_RCEPT_ENDDE",
                  "GNRL_RNK1_CRSPAREA_ENDDE", "GNRL_RNK1_ETC_AREA_ENDDE",
                  "GNRL_RNK2_CRSPAREA_ENDDE", "GNRL_RNK2_ETC_AREA_ENDDE")
    return bool(vals) and max(vals) >= TODAY


def schedule_of(d, apt=False):
    special = pick(d, "SPSPLY_RCEPT_BGNDE")
    result = pick(d, "PRZWNER_PRESNATN_DE")
    if apt:
        rank1 = pick(d, "GNRL_RNK1_CRSPAREA_RCPTDE", "RCEPT_BGNDE")
        rank2 = pick(d, "GNRL_RNK2_CRSPAREA_RCPTDE")
    else:
        rank1 = pick(d, "GNRL_RCEPT_BGNDE", "SUBSCRPT_RCEPT_BGNDE", "RCEPT_BGNDE")
        rank2 = ""
    opens = _dates(d, "SPSPLY_RCEPT_BGNDE", "RCEPT_BGNDE", "SUBSCRPT_RCEPT_BGNDE", "GNRL_RCEPT_BGNDE",
                   "GNRL_RNK1_CRSPAREA_RCPTDE", "GNRL_RNK1_ETC_AREA_RCPTDE")
    closes = _dates(d, "RCEPT_ENDDE", "SUBSCRPT_RCEPT_ENDDE", "GNRL_RCEPT_ENDDE", "SPSPLY_RCEPT_ENDDE",
                    "GNRL_RNK1_CRSPAREA_ENDDE", "GNRL_RNK1_ETC_AREA_ENDDE",
                    "GNRL_RNK2_CRSPAREA_ENDDE", "GNRL_RNK2_ETC_AREA_ENDDE")
    return {
        "notice": pick(d, "RCRIT_PBLANC_DE"), "special": special,
        "rank1": rank1, "rank2": rank2, "result": result,
        "open": min(opens) if opens else "", "close": max(closes) if closes else "",
    }


def base_fields(d, areas_by_no):
    no = pick(d, "HOUSE_MANAGE_NO")
    region = pick(d, "SUBSCRPT_AREA_CODE_NM") or "기타"
    return no, {
        "name": pick(d, "HOUSE_NM") or "이름 미상",
        "region": region,
        "metro": region in METRO,
        "area": area_str(areas_by_no.get(no, set())),
        "areaMin": min(areas_by_no.get(no, {0})) or None,
        "url": pick(d, "PBLANC_URL", "HMPG_ADRES"),
    }


# ===== 면적(주택형) =========================================================
def fetch_areas():
    areas = defaultdict(set)
    for name, fld in [("getAPTLttotPblancMdl", "HOUSE_TY"),
                      ("getUrbtyOfctlLttotPblancMdl", "EXCLUSE_AR"),
                      ("getRemndrLttotPblancMdl", "HOUSE_TY")]:
        try:
            for r in fetch_pages(name, 8, 1000):
                a = to_int_area(pick(r, fld, "HOUSE_TY", "EXCLUSE_AR"))
                no = pick(r, "HOUSE_MANAGE_NO")
                if no and a: areas[no].add(a)
        except Exception as e:  # noqa: BLE001
            log(f"면적 {name} 실패: {e}")
    log(f"면적 {len(areas)}개 공고")
    return areas


# ===== 공고 수집 ============================================================
def collect_notices():
    areas = fetch_areas()
    out = []

    def add(rows, build):
        for d in rows:
            if not pick(d, "HOUSE_MANAGE_NO") or not is_open(d):
                continue
            out.append(build(d))

    # 1) APT 분양
    try:
        rows = fetch_pages("getAPTLttotPblancDetail", 3, 100)
        log(f"APT 분양 {len(rows)}건")
        def build_apt(d):
            no, b = base_fields(d, areas)
            dtl, rent = pick(d, "HOUSE_DTL_SECD_NM"), pick(d, "RENT_SECD_NM")
            secd = pick(d, "HOUSE_SECD_NM")
            if "임대" in rent or "임대" in dtl:
                kind, cat = "임대", "오피스텔류"
            elif "신혼희망" in secd or "신혼희망" in dtl:
                kind, cat = "신혼희망", "apt"
            elif "국민" in dtl:
                kind, cat = "국민", "apt"
            else:
                kind, cat = "민영", "apt"
            b.update({
                "cat": cat, "aptKind": kind, "typeLabel": f"{kind} 분양",
                "hasSpecial": bool(pick(d, "SPSPLY_RCEPT_BGNDE")),
                "regulated": pick(d, "SPECLT_RDN_EARTH_AT") == "Y" or pick(d, "MDAT_TRGET_AREA_SECD") == "Y",
                "schedule": schedule_of(d, apt=True),
            })
            return b
        add(rows, build_apt)
    except Exception as e:  # noqa: BLE001
        log(f"APT 실패: {e}")

    # 2~5) 비APT 카테고리 (cat, label은 HOUSE_SECD_NM/HOUSE_DTL_SECD_NM 기준)
    cfg = [
        ("getRemndrLttotPblancDetail", 3, 300, "remndr"),
        ("getOPTLttotPblancDetail", 2, 300, "임의공급"),
        ("getUrbtyOfctlLttotPblancDetail", 2, 100, "오피스텔류"),
        ("getPblPvtRentLttotPblancDetail", 2, 100, "공공지원민간임대"),
    ]
    for name, pages, per, catkey in cfg:
        try:
            rows = fetch_pages(name, pages, per)
            log(f"{name} {len(rows)}건")
            def build(d, catkey=catkey):
                no, b = base_fields(d, areas)
                secnm = pick(d, "HOUSE_SECD_NM")
                if catkey == "remndr":
                    cat = "불법행위재공급" if "불법행위" in secnm else "무순위"
                    label = secnm or "무순위"
                elif catkey == "오피스텔류":
                    cat = "오피스텔류"; label = pick(d, "HOUSE_DTL_SECD_NM") or "오피스텔"
                else:
                    cat = catkey; label = secnm or catkey
                b.update({
                    "cat": cat, "aptKind": None, "typeLabel": label,
                    "hasSpecial": bool(pick(d, "SPSPLY_RCEPT_BGNDE")),
                    "regulated": False, "schedule": schedule_of(d, apt=False),
                })
                return b
            add(rows, build)
        except Exception as e:  # noqa: BLE001
            log(f"{name} 실패: {e}")

    out.sort(key=lambda n: n["schedule"].get("open") or n["schedule"].get("rank1") or "9999")
    return out


# ===== 지역 당첨 가점 밴드 ===================================================
def fetch_region_scores():
    try:
        rows = fetch_pages(STAT_BASE, 1, 1000, is_url=True)
    except Exception as e:  # noqa: BLE001
        log(f"지역 당첨 통계 실패: {e}")
        return {}
    best = {}
    for r in rows:
        if str(r.get("RESIDE_SECD", "")) != "01":
            continue
        region, de = pick(r, "SUBSCRPT_AREA_CODE_NM"), pick(r, "STAT_DE")
        try: avg = float(r.get("AVRG_SCORE"))
        except (TypeError, ValueError): continue
        if not region or avg <= 0: continue
        if region not in best or de > best[region][0]:
            def f(k):
                try: return round(float(r.get(k)))
                except (TypeError, ValueError): return None
            best[region] = (de, round(avg, 1), f("LWET_SCORE"), f("TOP_SCORE"))
    out = {k: {"month": f"{de[:4]}.{de[4:6]}" if len(de) >= 6 else de, "avg": avg, "low": lo, "top": tp}
           for k, (de, avg, lo, tp) in best.items()}
    log(f"지역 당첨 통계 {len(out)}개 지역")
    return out


def main():
    if not SERVICE_KEY:
        log("SERVICE_KEY 미설정 — data.json 유지하고 종료(데모).")
        return 0
    try:
        notices = collect_notices()
        region_scores = fetch_region_scores()
    except Exception as e:  # noqa: BLE001
        log(f"수집 실패: {e} — data.json 유지.")
        return 0
    if not notices:
        log("모집 중 공고 없음 — data.json 유지.")
        return 0
    payload = {
        "source": "github-actions", "live": True,
        "collectedAt": datetime.now(KST).isoformat(timespec="seconds"),
        "notices": notices, "regionScores": region_scores,
    }
    out = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data.json"))
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    log(f"data.json 작성 — 공고 {len(notices)}건, {payload['collectedAt']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
