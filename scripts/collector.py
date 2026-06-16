#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
청약홈 Open API 수집기 — 자격(순위/특공) + 카테고리 + 과거 비교단지
====================================================================
GitHub Actions에서 주기 실행되어 청약홈 공공데이터를 호출하고 data.json 으로 저장.

산출물
  notices       : 현재 모집 중·예정 공고 (카테고리·순위판정용 메타)
  regionScores  : 지역×월 당첨 가점(거친 폴백용)
  pastUnits     : 최근 24개월 민영 일반공급 '단지×면적' 풀
                  (지역·전용면적·평단가·최저/평균 당첨가점·1순위 경쟁률·공고월·URL)
                  → 프런트가 "같은 지역·비슷한 면적·비슷한 분양가"로 5개 추려 정밀 밴드.

데이터 소스 (모두 HOUSE_MANAGE_NO[+HOUSE_TY]로 조인)
  분양정보 getAPTLttotPblancDetail / getAPTLttotPblancMdl(면적·분양가)
  무순위 getRemndrLttotPblancDetail · 임의공급 getOPTLttotPblancDetail
  오피스텔 getUrbtyOfctlLttotPblancDetail · 공공지원민간임대 getPblPvtRentLttotPblancDetail
  당첨가점 getAptLttotPblancScore · 경쟁률 getAPTLttotPblancCmpet · 통계 getAPTApsPrzwnerStat

주의: 시세(실거래가)는 청약홈 API에 없음 → '분양가/시세 비율'은 미수집.
프런트에서 평단가 유사도로 근사하며 화면에 명시함.
SERVICE_KEY 없으면 data.json 유지하고 종료(데모).
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
DETAIL = "https://api.odcloud.kr/api/ApplyhomeInfoDetailSvc/v1/"
CMPET = "https://api.odcloud.kr/api/ApplyhomeInfoCmpetRtSvc/v1/"
STAT = "https://api.odcloud.kr/api/ApplyhomeStatSvc/v1/getAPTApsPrzwnerStat"

KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST).date().isoformat()
CUT24 = (datetime.now(KST).date() - timedelta(days=731)).isoformat()
METRO = {"서울", "경기", "인천"}


def log(msg): print(f"[collector] {msg}", flush=True)


def fetch_pages(url, pages, per):
    out = []
    for p in range(1, pages + 1):
        qs = urllib.parse.urlencode({"page": p, "perPage": per, "serviceKey": SERVICE_KEY})
        req = urllib.request.Request(f"{url}?{qs}", headers={"Accept": "application/json"})
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


def to_float(v):
    try: return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError, AttributeError): return None


def parse_rate(v):
    """'9.88','312.5:1','-','△' → float. 파싱 불가 -1, 미달/없음 0."""
    if v is None: return -1.0
    s = str(v).strip()
    if s in ("", "-", "△", "미달", "접수없음"): return 0.0
    s = s.replace(":1", "").replace(":", "").replace(",", "").strip()
    try: return float(s)
    except ValueError: return -1.0


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
    vals = _dates(d, "PRZWNER_PRESNATN_DE", "RCEPT_ENDDE", "SUBSCRPT_RCEPT_ENDDE",
                  "GNRL_RCEPT_ENDDE", "SPSPLY_RCEPT_ENDDE",
                  "GNRL_RNK1_CRSPAREA_ENDDE", "GNRL_RNK1_ETC_AREA_ENDDE",
                  "GNRL_RNK2_CRSPAREA_ENDDE", "GNRL_RNK2_ETC_AREA_ENDDE")
    return bool(vals) and max(vals) >= TODAY


def schedule_of(d, apt=False):
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
        "notice": pick(d, "RCRIT_PBLANC_DE"), "special": pick(d, "SPSPLY_RCEPT_BGNDE"),
        "rank1": rank1, "rank2": rank2, "result": pick(d, "PRZWNER_PRESNATN_DE"),
        "open": min(opens) if opens else "", "close": max(closes) if closes else "",
    }


def base_fields(d, areas, price):
    no = pick(d, "HOUSE_MANAGE_NO")
    region = pick(d, "SUBSCRPT_AREA_CODE_NM") or "기타"
    amin = min(areas.get(no, {0})) or None
    return no, {
        "name": pick(d, "HOUSE_NM") or "이름 미상",
        "region": region, "metro": region in METRO,
        "area": area_str(areas.get(no, set())), "areaMin": amin,
        "pyeong": price.get((no, amin)) if amin else None,
        "url": pick(d, "PBLANC_URL", "HMPG_ADRES"),
    }


# ===== 면적 + 평단가 ========================================================
def fetch_model():
    areas = defaultdict(set)
    price = {}   # (no, area) -> 평단가(만원/평)
    try:
        for r in fetch_pages(DETAIL + "getAPTLttotPblancMdl", 14, 1000):
            no, a = pick(r, "HOUSE_MANAGE_NO"), to_int_area(pick(r, "HOUSE_TY"))
            if not no or not a: continue
            areas[no].add(a)
            amt, ar = to_float(r.get("LTTOT_TOP_AMOUNT")), to_float(r.get("SUPLY_AR"))
            if amt and ar and amt > 0 and ar > 0:
                price[(no, a)] = max(price.get((no, a), 0), round(amt / (ar / 3.3058)))
    except Exception as e:  # noqa: BLE001
        log(f"APT 주택형 실패: {e}")
    for name, fld in [("getUrbtyOfctlLttotPblancMdl", "EXCLUSE_AR"), ("getRemndrLttotPblancMdl", "HOUSE_TY")]:
        try:
            for r in fetch_pages(DETAIL + name, 8, 1000):
                no, a = pick(r, "HOUSE_MANAGE_NO"), to_int_area(pick(r, fld, "HOUSE_TY", "EXCLUSE_AR"))
                if no and a: areas[no].add(a)
        except Exception as e:  # noqa: BLE001
            log(f"면적 {name} 실패: {e}")
    log(f"면적 {len(areas)}개 공고 · 평단가 {len(price)}개 주택형")
    return areas, price


# ===== 현재 공고 ============================================================
def collect_notices(apt_rows, areas, price):
    out = []

    def add(rows, build):
        for d in rows:
            if pick(d, "HOUSE_MANAGE_NO") and is_open(d):
                out.append(build(d))

    def build_apt(d):
        no, b = base_fields(d, areas, price)
        dtl, rent, secd = pick(d, "HOUSE_DTL_SECD_NM"), pick(d, "RENT_SECD_NM"), pick(d, "HOUSE_SECD_NM")
        if "임대" in rent or "임대" in dtl: kind, cat = "임대", "오피스텔류"
        elif "신혼희망" in secd or "신혼희망" in dtl: kind, cat = "신혼희망", "apt"
        elif "국민" in dtl: kind, cat = "국민", "apt"
        else: kind, cat = "민영", "apt"
        b.update({"cat": cat, "aptKind": kind, "typeLabel": f"{kind} 분양",
                  "hasSpecial": bool(pick(d, "SPSPLY_RCEPT_BGNDE")),
                  "regulated": pick(d, "SPECLT_RDN_EARTH_AT") == "Y" or pick(d, "MDAT_TRGET_AREA_SECD") == "Y",
                  "schedule": schedule_of(d, apt=True)})
        return b
    add(apt_rows, build_apt)

    cfg = [("getRemndrLttotPblancDetail", 3, 300, "remndr"),
           ("getOPTLttotPblancDetail", 2, 300, "임의공급"),
           ("getUrbtyOfctlLttotPblancDetail", 2, 100, "오피스텔류"),
           ("getPblPvtRentLttotPblancDetail", 2, 100, "공공지원민간임대")]
    for name, pages, per, catkey in cfg:
        try:
            rows = fetch_pages(DETAIL + name, pages, per)
            log(f"{name} {len(rows)}건")

            def build(d, catkey=catkey):
                no, b = base_fields(d, areas, price)
                secnm = pick(d, "HOUSE_SECD_NM")
                if catkey == "remndr":
                    cat = "불법행위재공급" if "불법행위" in secnm else "무순위"; label = secnm or "무순위"
                elif catkey == "오피스텔류":
                    cat = "오피스텔류"; label = pick(d, "HOUSE_DTL_SECD_NM") or "오피스텔"
                else:
                    cat = catkey; label = secnm or catkey
                b.update({"cat": cat, "aptKind": None, "typeLabel": label,
                          "hasSpecial": bool(pick(d, "SPSPLY_RCEPT_BGNDE")),
                          "regulated": False, "schedule": schedule_of(d, apt=False)})
                return b
            add(rows, build)
        except Exception as e:  # noqa: BLE001
            log(f"{name} 실패: {e}")

    out.sort(key=lambda n: n["schedule"].get("open") or n["schedule"].get("rank1") or "9999")
    return out


# ===== 과거 비교단지 풀 (민영 일반공급 · 24개월) =============================
def collect_past_units(apt_rows, areas, price):
    # 당첨 최저/평균 가점 (해당지역)
    score = defaultdict(lambda: {"low": [], "avg": []})
    try:
        for r in fetch_pages(CMPET + "getAptLttotPblancScore", 16, 1000):
            if str(r.get("RESIDE_SECD", "")) != "01": continue
            no, a = pick(r, "HOUSE_MANAGE_NO"), to_int_area(pick(r, "HOUSE_TY"))
            lw, av = to_float(r.get("LWET_SCORE")), to_float(r.get("AVRG_SCORE"))
            if no and a and lw and lw > 0:
                score[(no, a)]["low"].append(lw)
                if av and av > 0: score[(no, a)]["avg"].append(av)
    except Exception as e:  # noqa: BLE001
        log(f"당첨가점 실패: {e}")
    # 1순위 해당지역 경쟁률
    rate = defaultdict(list)
    try:
        for r in fetch_pages(CMPET + "getAPTLttotPblancCmpet", 16, 1000):
            if r.get("SUBSCRPT_RANK_CODE") != 1 or str(r.get("RESIDE_SECD", "")) != "01": continue
            no, a = pick(r, "HOUSE_MANAGE_NO"), to_int_area(pick(r, "HOUSE_TY"))
            cr = parse_rate(r.get("CMPET_RATE"))
            if no and a and cr > 0: rate[(no, a)].append(cr)
    except Exception as e:  # noqa: BLE001
        log(f"경쟁률 실패: {e}")
    # 민영 24개월 메타
    meta = {}
    for d in apt_rows:
        if "민영" not in pick(d, "HOUSE_DTL_SECD_NM"): continue
        ym = pick(d, "RCRIT_PBLANC_DE")
        if not ym or ym < CUT24: continue
        meta[pick(d, "HOUSE_MANAGE_NO")] = {
            "name": pick(d, "HOUSE_NM") or "이름 미상",
            "region": pick(d, "SUBSCRPT_AREA_CODE_NM") or "기타",
            "url": pick(d, "PBLANC_URL", "HMPG_ADRES"), "ym": ym[:7],
        }
    units = []
    for (no, a), sc in score.items():
        m = meta.get(no)
        if not m or not sc["low"]: continue
        rr = rate.get((no, a))
        units.append({
            "name": m["name"], "region": m["region"], "area": a, "url": m["url"], "ym": m["ym"],
            "pyeong": price.get((no, a)),
            "low": round(min(sc["low"])),
            "avg": round(sum(sc["avg"]) / len(sc["avg"])) if sc["avg"] else round(min(sc["low"])),
            "rate": round(sum(rr) / len(rr), 1) if rr else None,
        })
    units.sort(key=lambda u: (u["region"], u["area"]))
    log(f"과거 비교단지 {len(units)}개 (민영·24개월)")
    return units


# ===== 지역 당첨 가점(폴백) =================================================
def fetch_region_scores():
    try:
        rows = fetch_pages(STAT, 1, 1000)
    except Exception as e:  # noqa: BLE001
        log(f"지역 통계 실패: {e}")
        return {}
    best = {}
    for r in rows:
        if str(r.get("RESIDE_SECD", "")) != "01": continue
        region, de, avg = pick(r, "SUBSCRPT_AREA_CODE_NM"), pick(r, "STAT_DE"), to_float(r.get("AVRG_SCORE"))
        if not region or not avg or avg <= 0: continue
        if region not in best or de > best[region][0]:
            best[region] = (de, round(avg, 1),
                            round(to_float(r.get("LWET_SCORE"))) if to_float(r.get("LWET_SCORE")) else None,
                            round(to_float(r.get("TOP_SCORE"))) if to_float(r.get("TOP_SCORE")) else None)
    out = {k: {"month": f"{de[:4]}.{de[4:6]}" if len(de) >= 6 else de, "avg": avg, "low": lo, "top": tp}
           for k, (de, avg, lo, tp) in best.items()}
    log(f"지역 통계 {len(out)}개")
    return out


def main():
    if not SERVICE_KEY:
        log("SERVICE_KEY 미설정 — data.json 유지(데모).")
        return 0
    try:
        areas, price = fetch_model()
        apt_rows = fetch_pages(DETAIL + "getAPTLttotPblancDetail", 12, 100)   # 24개월 커버
        log(f"APT 분양 {len(apt_rows)}건")
        notices = collect_notices(apt_rows, areas, price)
        region_scores = fetch_region_scores()
        past_units = collect_past_units(apt_rows, areas, price)
    except Exception as e:  # noqa: BLE001
        log(f"수집 실패: {e} — data.json 유지.")
        return 0
    if not notices:
        log("모집 중 공고 없음 — data.json 유지.")
        return 0
    payload = {
        "source": "github-actions", "live": True,
        "collectedAt": datetime.now(KST).isoformat(timespec="seconds"),
        "notices": notices, "regionScores": region_scores, "pastUnits": past_units,
    }
    out = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data.json"))
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    log(f"data.json 작성 — 공고 {len(notices)} · 과거단지 {len(past_units)} · {payload['collectedAt']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
