"""
뉴스 자동 수집 & 구글 시트 자동화 (글로벌 확장판)
- 네이버 뉴스 API: 한국어(KR) 뉴스 수집
- Google News RSS: 다국어(EN, JP, CN, HI, VN) 뉴스 수집
- Google Sheets API로 자동 저장 (중복 방지)
- 언어별 맞춤형 블랙리스트(주식/금융 필터링) 적용
- 오늘 + 어제 뉴스 모두 수집
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

# 검색 키워드
KEYWORDS = ["키움DRX", "DRX", "KRX", "디알엑스", "키움디알엑스", "KIWOOM DRX", "LazyFeel"]

# 해외 뉴스를 가져올 언어 및 국가 코드 설정
TARGET_LANGS = {
    "EN": "hl=en-US&gl=US&ceid=US:en",
    "JP": "hl=ja&gl=JP&ceid=JP:ja",
    "CN": "hl=zh-CN&gl=CN&ceid=CN:zh",
    "HI": "hl=hi&gl=IN&ceid=IN:hi",
    "VN": "hl=vi&gl=VN&ceid=VN:vi"  
}

# 💡 언어와 상관없이 무조건 거를 '공통 블랙리스트'
GLOBAL_EXCLUDE = ["drax", "kospi", "kosdaq"]

# 💡 언어별 맞춤형 제외어 사전 (e스포츠 기사가 걸러지지 않도록 주의해서 구성)
EXCLUDE_WORDS_BY_LANG = {
    "KR": [
        "주가", "주식", "증시", "코스피", "코스닥", "특징주", "목표가", "매수",
        "거래소", "금융", "펀드", "키움증권", "프로야구"
    ],
    "EN": [
        "stock", "exchange", "shares", "invest", "finance", "securities"
    ],
    "JP": [
        "株", "株式", "証券", "取引所", "金融", "投資"
    ],
    "CN": [
        "股票", "股市", "证券", "交易所", "金融", "投资"
    ],
    "HI": [
        "शेयर", "स्टॉक", "निवेश", "वित्त", "एक्सचेंज"
    ],
    "VN": [
        "cổ phiếu", "chứng khoán", "sàn giao dịch", "tài chính", "đầu tư"
    ]
}

# 네이버 API
NAVER_CLIENT_ID = os.environ.get("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "")

# 구글 시트
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "{}")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "")
SHEET_NAME = os.environ.get("SHEET_NAME", "시트1")

# 컬럼 매핑 (0-indexed)
COL_CATEGORY = 0
COL_DATE = 1
COL_MEDIA = 2
COL_LANG = 3
COL_TITLE = 4
COL_URL = 5

def get_target_dates():
    """어제와 오늘 날짜를 모두 반환"""
    kst = timezone(timedelta(hours=9))
    today = datetime.now(kst)
    yesterday = today - timedelta(days=1)
    return [today.strftime("%Y-%m-%d"), yesterday.strftime("%Y-%m-%d")]

def _date_key() -> str:
    return datetime.now().strftime("%Y-%m-%d")


# ── API 호출 함수들 ───────────────────────────────

def fetch_naver_news(keyword: str, start: int = 1, display: int = 100) -> list[dict]:
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
            item["lang"] = "KR"  
        return items
    except Exception as e:
        logger.error(f"네이버 API 호출 실패 ({keyword}): {e}")
        return []

def fetch_global_news(keyword: str, lang_code: str) -> list[dict]:
    valid_global_keywords = ["DRX", "KRX", "KIWOOM DRX", "LazyFeel"]
    if keyword not in valid_global_keywords:
        return []

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
    all_items: dict[str, dict] = {} 
    
    for kw in KEYWORDS:
        if kw != "LazyFeel":
            kr_items = fetch_naver_news(kw)
            logger.info(f"'{kw}' 네이버 뉴스(KR) 검색: {len(kr_items)}개")
            for item in kr_items:
                all_items[item["title"]] = item
            
        for lang in TARGET_LANGS.keys():
            global_items = fetch_global_news(kw, lang)
            if global_items:
                logger.info(f"'{kw}' 구글 뉴스({lang}) 검색: {len(global_items)}개")
            for item in global_items:
                all_items[item["title"]] = item
                
    return list(all_items.values())


# ── 구문 처리 ──────────────────────────────────────────

def normalize_date(date_str):
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
        "",                                
        normalize_date(item["pubDate"]),   
        "",                                
        item.get("lang", "KR"),            
        title,                             
        item["link"]                       
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

    # 💡 오늘과 어제 날짜 모두 수집하도록 변경된 부분
    target_dates = get_target_dates()
    new_rows = []
    
    for item in items:
        article_lang = item.get("lang", "KR")
        exclude_list = GLOBAL_EXCLUDE + EXCLUDE_WORDS_BY_LANG.get(article_lang, [])
        
        title_and_desc = item.get("title", "") + " " + item.get("description", "")
        title_and_desc_lower = title_and_desc.lower()
        
        # 언어별 맞춤 블랙리스트 필터링 작동
        if any(bad_word.lower() in title_and_desc_lower for bad_word in exclude_list):
            continue

        row = extract_row(item)
        
        # 💡 오늘 또는 어제 기사가 아니면 건너뛰기
        if row[COL_DATE] not in target_dates:
            continue
            
        article_url = row[COL_URL]
        if article_url not in existing_keys:
            new_rows.append(row)
            existing_keys.add(article_url)

    append_rows(sheet, new_rows)
    logger.info("✅ 뉴스 수집 완료 (%d개 신규)", len(new_rows))

if __name__ == "__main__":
    main()
