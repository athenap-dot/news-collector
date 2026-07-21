"""
뉴스 자동 수집 & 구글 시트 자동화 (글로벌 확장판)
- 네이버 뉴스 API: 한국어(KR) 뉴스 수집
- Google News RSS: 다국어(EN, JP, CN, HI, VN) 뉴스 수집
- Google Sheets API로 자동 저장 (중복 방지)
"""

import os
import sys
import logging
import json
import hashlib
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen

import gspread

# ── 설정 ──────────────────────────────────────────────
LOGGING_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOGGING_FORMAT)
logger = logging.getLogger(__name__)

# LazyFeel 키워드 추가 (베트남어 전용으로 사용됨)
KEYWORDS = ["키움DRX", "DRX", "KRX", "디알엑스", "키움디알엑스", "KIWOOM DRX", "LazyFeel"]

# 해외 뉴스를 가져올 언어 및 국가 코드 설정 (구글 뉴스 RSS 용)
# 요청하신 라벨(EN, JP, CN, HI, VN)에 맞게 키(Key) 값 수정 및 베트남어(VN) 추가
TARGET_LANGS = {
    "EN": "hl=en-US&gl=US&ceid=US:en",
    "JP": "hl=ja&gl=JP&ceid=JP:ja",
    "CN": "hl=zh-CN&gl=CN&ceid=CN:zh",
    "HI": "hl=hi&gl=IN&ceid=IN:hi",
    "VN": "hl=vi&gl=VN&ceid=VN:vi"  # 베트남어 추가
}

# 추가할 블랙리스트 (이 단어가 포함된 뉴스는 수집 거부)
EXCLUDE_WORDS = [
    # 한국어 블랙리스트
    "주가", "주식", "증시", "코스피", "코스닥", "특징주", "목표가", "상장", "매수",
    "거래소", "금융", "펀드", "키움증권", "히어로즈", "프로야구", "야구",
    
    # 영어/글로벌 블랙리스트 (특히 KRX 검색 시 한국거래소 금융 기사 필터링 용도)
    "stock", "exchange", "shares", "invest", "finance", "market", "trading", 
    "kospi", "kosdaq", "baseball", "heroes"
]

# 네이버 API
NAVER_CLIENT_ID = os.environ.get("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "")

# 구글 시트
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "{}")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "")
SHEET_NAME = os.environ.get("SHEET_NAME", "시트1")

# 컬럼 매핑 (0-indexed)
COL_CATEGORY = 0   # 카테고리 (빈 칸)
COL_DATE = 1       # 기사 발행일
COL_MEDIA = 2      # 언론사명
COL_LANG = 3       # 기사 언어
COL_TITLE = 4      # 뉴스 제목
COL_URL = 5        # 기사 링크

def get_target_date():
    """한국 시간(KST) 기준으로 수집할 날짜 계산"""
    kst = timezone(timedelta(hours=9))
    # 아침 8시에 실행해서 '어제' 뉴스를 수집하려면 아래 줄 사용
    target = datetime.now(kst) - timedelta(days=1)
    return target.strftime("%Y-%m-%d")

def _date_key() -> str:
    """오늘 날짜 문자열 (YYYY-MM-DD)."""
    return datetime.now().strftime("%Y-%m-%d")


# ── API 호출 함수들 ───────────────────────────────

def fetch_naver_news(keyword: str, start: int = 1, display: int = 100) -> list[dict]:
    """네이버 뉴스 검색 API 호출 (한국어 전용)."""
    url = "https://openapi.naver.com/v1/search/news"
    params = urlencode({"query": keyword, "display": display, "start": start, "sort": "date"})
    req = Request(f"{url}?{params}")
    req.add_header("X-Naver-Client-Id", NAVER_CLIENT_ID)
    req.add_header("X-Naver-Client-Secret", NAVER_CLIENT_SECRET)

    try:
        resp = urlopen(req)
        data = json.loads(resp.read().decode("utf-8"))
        items = data.get("items", [])
        for item in items:
            item["lang"] = "KR"  # 네이버는 KR로 고정
        return items
    except Exception as e:
        logger.error(f"네이버 API 호출 실패 ({keyword}): {e}")
        return []

def fetch_global_news(keyword: str, lang_code: str) -> list[dict]:
    """구글 뉴스 RSS를 이용한 해외 뉴스 수집."""
    
    # 해외 뉴스 검색 시 허용할 영문 키워드 목록
    valid_global_keywords = ["DRX", "KRX", "KIWOOM DRX", "LazyFeel"]
    
    # 한글 키워드는 해외 구글 뉴스 검색에서 제외
    if keyword not in valid_global_keywords:
        return []

    # 💡 LazyFeel 키워드는 베트남어(VN) 뉴스 검색에만 사용하도록 분기 처리
    if keyword == "LazyFeel" and lang_code != "VN":
        return []

    url_params = TARGET_LANGS.get(lang_code)
    query = quote(f'"{keyword}"')
    url = f"https://news.google.com/rss/search?q={query}&{url_params}"
    
    try:
        req = Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        resp = urlopen(req)
        root = ET.fromstring(resp.read())
        
        items = []
        for item in root.findall('.//item'):
            title = item.find('title').text if item.find('title') is not None else ""
            link = item.find('link').text if item.find('link') is not None else ""
            pub_date = item.find('pubDate').text if item.find('pubDate') is not None else ""
            
            items.append({
                "title": title,
                "link": link,
                "pubDate": pub_date,
                "description": "", 
                "lang": lang_code
            })
        return items
    except Exception as e:
        logger.error(f"구글 뉴스 RSS 호출 실패 ({keyword}, {lang_code}): {e}")
        return []

def search_news() -> list[dict]:
    """모든 키워드로 다국어 뉴스 검색 후 반환."""
    all_items: dict[str, dict] = {}  # title 키로 중복 제거
    
    for kw in KEYWORDS:
        # 1. 한국어 뉴스 (네이버 API)
        # LazyFeel은 한국어 검색에서 제외
        if kw != "LazyFeel":
            kr_items = fetch_naver_news(kw)
            logger.info(f"'{kw}' 네이버 뉴스(KR) 검색: {len(kr_items)}개")
            for item in kr_items:
                all_items[item["title"]] = item
            
        # 2. 다국어 뉴스 (구글 RSS)
        for lang in TARGET_LANGS.keys():
            global_items = fetch_global_news(kw, lang)
            if global_items:
                logger.info(f"'{kw}' 구글 뉴스({lang}) 검색: {len(global_items)}개")
            for item in global_items:
                all_items[item["title"]] = item
                
    return list(all_items.values())


# ── 구문 처리 ──────────────────────────────────────────

def normalize_date(date_str):
    """RFC 2822 형식의 날짜를 KST 기준 YYYY-MM-DD로 변환"""
    if not date_str:
        return ""
    try:
        dt = parsedate_to_datetime(date_str)
        kst = timezone(timedelta(hours=9))
        kst_dt = dt.astimezone(kst)
        return kst_dt.strftime("%Y-%m-%d")
    except Exception:
        return date_str

def extract_row(item):
    title = item.get("title", "").replace("<b>", "").replace("</b>", "").replace("&quot;", "\"")
    
    return [
        "",                                # 1. Category
        normalize_date(item["pubDate"]),   # 2. Date
        "",                                # 3. Media Name
        item.get("lang", "KR"),            # 4. Language (KR, EN, JP, CN, HI, VN)
        title,                             # 5. Title
        item["link"]                       # 6. URL
    ]


# ── Google Sheets ──────────────────────────────────────

def get_sheet() -> gspread.Worksheet:
    gc = gspread.service_account_from_dict(json.loads(GOOGLE_SERVICE_ACCOUNT_JSON))
    ss = gc.open_by_key(SPREADSHEET_ID)
    return ss.worksheet(SHEET_NAME)

def ensure_headers(sheet: gspread.Worksheet) -> None:
    headers = ["Category", "Date", "Media Name", "Language", "Title", "URL"]
    existing = sheet.row_values(1)
    if existing == headers:
        return
    sheet.update_cell(1, 1, headers[0])
    for i, h in enumerate(headers[1:], 2):
        sheet.update_cell(1, i, h)
    logger.info("헤더 초기화 완료")

def get_existing_keys(sheet: gspread.Worksheet) -> set[str]:
    return set(sheet.col_values(6))

def append_rows(sheet: gspread.Worksheet, rows: list[list[str]]) -> None:
    if rows:
        sheet.append_rows(rows, value_input_option="RAW")
        logger.info(f"{len(rows)}개 행 추가 완료")


# ── 메인 ───────────────────────────────────────────────

def main() -> None:
    logger.info("=" * 50)
    logger.info("🚀 다국어 뉴스 수집 시작 (%s)", _date_key())

    items = search_news()
    if not items:
        logger.warning("수집된 뉴스 없음 → 종료")
        return

    sheet = get_sheet()
    ensure_headers(sheet)
    existing_keys = get_existing_keys(sheet)

    target_date = get_target_date()
    new_rows = []
    
    for item in items:
        title_and_desc = item.get("title", "") + " " + item.get("description", "")
        # 대소문자 구분 없이 블랙리스트 필터링 적용 (영문 필터링을 위해 lower() 사용)
        title_and_desc_lower = title_and_desc.lower()
        if any(bad_word.lower() in title_and_desc_lower for bad_word in EXCLUDE_WORDS):
            continue

        row = extract_row(item)
        
        # 날짜 체크
        if row[COL_DATE] != target_date:
            continue
            
        # 중복 체크
        article_url = row[COL_URL]
        if article_url not in existing_keys:
            new_rows.append(row)
            existing_keys.add(article_url)

    append_rows(sheet, new_rows)
    logger.info("✅ 뉴스 수집 완료 (%d개 신규)", len(new_rows))

if __name__ == "__main__":
    main()
