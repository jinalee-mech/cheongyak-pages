# -*- coding: utf-8 -*-
"""
국토부 실거래가 공개시스템(rt.molit.go.kr) 내부 엔드포인트로 시세 조회.
data.go.kr OpenAPI(RTMS)가 다운(502)일 때 대체 경로. 브라우저 UA만 붙이면 됨(헤드리스 불필요).

흐름 (모두 단순 POST)
  /cmm/emdList.do        code=시군구5자리        → 읍면동 [{code:10자리, codeNm}]
  /pt/gis/ptDanjiList.do srhLedCd=동10자리,srhYear → 단지 [{aprpnHsmpCode, aprpnHsmpNm}]
  /pt/gis/ptDtl.do       srhAprpnHsmpCode,dtlYear  → 거래 [{prvuseAr, thingAmount, cntrctDe}]

주의: 비공식 내부 엔드포인트라 사이트 개편 시 깨질 수 있음(공식 경로는 data.go.kr RTMS).
"""
import json
import urllib.parse
import urllib.request
from collections import defaultdict

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
BASE = "https://rt.molit.go.kr"
HDRS = {"User-Agent": UA, "Referer": f"{BASE}/pt/gis/gis.do",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}

_emd_cache = {}      # 시군구5 -> {동명: 동10}
_danji_cache = {}    # (동10, year) -> [aprpnHsmpCode]
_trade_cache = {}    # (단지코드, year) -> [(전용면적, 거래금액만원, 계약일)]
DANJI_LIMIT = 30     # 동당 단지 상한(과도호출 방지)


def _post(path, data, timeout=20):
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(f"{BASE}{path}", data=body, headers=HDRS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def probe():
    """엔드포인트 살아있는지(시군구 목록 응답). 실패 시 예외."""
    _post("/cmm/emdList.do", {"code": "11680"})


def _emd(sigungu5):
    if sigungu5 in _emd_cache:
        return _emd_cache[sigungu5]
    out = {}
    try:
        for x in _post("/cmm/emdList.do", {"code": sigungu5}).get("emdList", []):
            if x.get("codeNm") and x.get("code"):
                out[x["codeNm"].strip()] = x["code"].strip()
    except Exception:  # noqa: BLE001
        pass
    _emd_cache[sigungu5] = out
    return out


def _danji(led10, year):
    key = (led10, year)
    if key in _danji_cache:
        return _danji_cache[key]
    codes = []
    try:
        for page in (1, 2):
            d = _post("/pt/gis/ptDanjiList.do", {
                "srhThingSecd": "A", "srhYear": year, "srhLadSecd": "1",
                "srhLedCd": led10, "srhRoadCd": "", "srhBldgNm": "", "pageIndex": page, "mobileAt": ""})
            lst = d.get("danjiList") or []
            codes += [x["aprpnHsmpCode"] for x in lst if x.get("aprpnHsmpCode")]
            if len(lst) < 15:
                break
    except Exception:  # noqa: BLE001
        pass
    codes = codes[:DANJI_LIMIT]
    _danji_cache[key] = codes
    return codes


def _trades(code, year):
    key = (code, year)
    if key in _trade_cache:
        return _trade_cache[key]
    out = []
    try:
        d = _post("/pt/gis/ptDtl.do", {
            "srhThingSecd": "A", "srhDelngSecd": "1", "dtlYear": year, "dtlMon": "",
            "dtlArea": "", "dtlAmount": "0", "srhAprpnHsmpCode": code})
        for x in d.get("danjiList") or []:
            try:
                ar = float(x.get("prvuseAr"))
                amt = int(str(x.get("thingAmount")).replace(",", "").strip())
                out.append((ar, amt, str(x.get("cntrctDe", ""))))
            except (ValueError, TypeError):
                continue
    except Exception:  # noqa: BLE001
        pass
    _trade_cache[key] = out
    return out


def _match_dong(emap, dong):
    if not dong:
        return None
    if dong in emap:
        return emap[dong]
    for nm, code in emap.items():
        if nm.startswith(dong[:2]) or dong in nm or nm in dong:
            return code
    return None


def sise_amounts(sigungu_codes, dong, area, years):
    """인근 동일 읍면동·유사 전용면적(±3㎡) 실거래 금액(만원) 리스트. 최근연도 우선."""
    for sg in sigungu_codes:
        led = _match_dong(_emd(sg), dong)
        if not led:
            continue
        amounts = []
        for y in years:
            for code in _danji(led, y):
                for ar, amt, _de in _trades(code, y):
                    if abs(ar - area) <= 3:
                        amounts.append(amt)
            if len(amounts) >= 5:
                break
        if len(amounts) >= 3:
            return amounts
    return []
