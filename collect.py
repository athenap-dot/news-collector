"""
뉴스 자동 수집 & 구글 시트 자동화
- 네이버 뉴스 API에서 특정 키워드 뉴스 수집
- Google Sheets API로 자동 저장 (중복 방지)
- GitHub Actions에서 매일 오전 8시(KST) 자동 실행
"""

import os
import sys
import logging
import json
import hashlib
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import gspread

# ── 설정 ──────────────────────────────────────────────
LOGGING_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOGGING_FORMAT)
logger = logging.getLogger(__name__)

KEYWORDS = ["키움DRX", "DRX", "KRX", "디알엑스", "키움디알엑스"]

# 추가할 블랙리스트 (이 단어가 포함된 뉴스는 수집 거부)
EXCLUDE_WORDS = [
    "주가", "주식", "증시", "코스피", "코스닥", "특징주", "목표가", "상장", "매수",
    "거래소", "금융", "펀드", "키움증권", "히어로즈", "프로야구", "야구"
]

# 네이버 API (https://developers.naver.com/main/)
NAVER_CLIENT_ID = os.environ.get("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "")

# 구글 시트
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get(
    "GOOGLE_SERVICE_ACCOUNT_JSON", "{}"
)
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
    
    # 만약 '오늘' 뉴스를 수집하고 싶다면 위 줄을 지우고 아래 줄 사용
    # target = datetime.now(kst)
    
    return target.strftime("%Y-%m-%d")

def _date_key() -> str:
    """오늘 날짜 문자열 (YYYY-MM-DD)."""
    return datetime.now().strftime("%Y-%m-%d")


def _row_key(row: list[str]) -> str:
    """기사 고유 키 (발행일+제목) → 중복 감지용."""
    # Date(row[1])와 Title(row[4])을 합쳐서 중복을 판별합니다.
    return hashlib.sha256(
        f"{row[COL_DATE]}|{row[COL_TITLE]}".encode()
    ).hexdigest()


# ── 네이버 뉴스 API 호출 ───────────────────────────────
def fetch_naver_news(keyword: str, start: int = 1, display: int = 100) -> list[dict]:
    """네이버 뉴스 검색 API 호출."""
    url = "https://openapi.naver.com/v1/search/news"
    params = urlencode({"query": keyword, "display": display, "start": start, "sort": "date"})
    req = Request(f"{url}?{params}")
    req.add_header("X-Naver-Client-Id", NAVER_CLIENT_ID)
    req.add_header("X-Naver-Client-Secret", NAVER_CLIENT_SECRET)

    try:
        resp = urlopen(req)
        data = json.loads(resp.read().decode("utf-8"))
        return data.get("items", [])
    except Exception as e:
        logger.error(f"네이버 API 호출 실패 ({keyword}): {e}")
        return []


def search_news() -> list[dict]:
    """모든 키워드로 뉴스 검색 후 반환."""
    all_items: dict[str, dict] = {}  # title 키로 중복 제거
    for kw in KEYWORDS:
        items = fetch_naver_news(kw)
        logger.info(f"'{kw}' 검색: {len(items)}개")
        for item in items:
            all_items[item["title"]] = item
    return list(all_items.values())


# ── 구문 처리 ──────────────────────────────────────────
def normalize_date(date_str):
    try:
        # 네이버 API 날짜(예: Mon, 20 Jul 2026 10:39:00 +0900)를 시간 객체로 변환
        dt = datetime.strptime(date_str, "%a, %d %b %Y %H:%M:%S %z")
        # YYYY-MM-DD 형식으로 출력
        return dt.strftime("%Y-%m-%d")
    except Exception:
        # 혹시 날짜 형식이 다를 경우 원본 텍스트 그대로 반환
        return date_str


def extract_row(item):
    # <b>, &quot; 등 불필요한 HTML 태그와 기호 제거
    title = item.get("title", "").replace("<b>", "").replace("</b>", "").replace("&quot;", "\"")
    
    return [
        "",                               # 1. Category (수집 단계에선 빈 칸)
        normalize_date(item["pubDate"]),  # 2. Date (기사 발행일)
        "",                               # 3. Media Name (네이버 API는 언론사명을 주지 않으므로 빈 칸)
        "KR",                             # 4. Language (KR 표기)
        title,                            # 5. Title (기사 제목)
        item["link"]                      # 6. URL (기사 링크)
    ]

# ── Google Sheets ──────────────────────────────────────
def get_sheet() -> gspread.Worksheet:
    """서비스 계정으로 시트 연결."""
    gc = gspread.service_account_from_dict(json.loads(GOOGLE_SERVICE_ACCOUNT_JSON))
    ss = gc.open_by_key(SPREADSHEET_ID)
    return ss.worksheet(SHEET_NAME)


def ensure_headers(sheet: gspread.Worksheet) -> None:
    """헤더가 없으면 삽입."""
    headers = ["Category", "Date", "Media Name", "Language", "Title", "URL"]
    existing = sheet.row_values(1)
    if existing == headers:
        return
    sheet.update_cell(1, 1, headers[0])
    for i, h in enumerate(headers[1:], 2):
        sheet.update_cell(1, i, h)
    logger.info("헤더 초기화 완료")


def get_existing_keys(sheet: gspread.Worksheet) -> set[str]:
    """기존 행들의 키 집합."""
    rows = sheet.get_all_records()
    return {_row_key(list(r.values())) for r in rows}


def append_rows(sheet: gspread.Worksheet, rows: list[list[str]]) -> None:
    """데이터를 시트에 추가."""
    sheet.append_rows(rows, value_input_option="RAW")
    logger.info(f"{len(rows)}개 행 추가 완료")


# ── 메인 ───────────────────────────────────────────────
def main() -> None:
    logger.info("=" * 50)
    logger.info("🚀 뉴스 수집 시작 (%s)", _date_key())
    logger.info("키워드: %s", ", ".join(KEYWORDS))

    # 1) 뉴스 수집
    items = search_news()
    if not items:
        logger.warning("수집된 뉴스 없음 → 종료")
        return

    # 2) 시트 연결
    sheet = get_sheet()
    ensure_headers(sheet)
    existing_keys = get_existing_keys(sheet)

    # 3) 중복, 당일 날짜, 금지어 필터링
    target_date = get_target_date()
    new_rows = []
    for item in items:
        # ▼▼▼ 여기부터 새로 추가할 금지어 검사 로직 ▼▼▼
        title_and_desc = item.get("title", "") + " " + item.get("description", "")
        if any(bad_word in title_and_desc for bad_word in EXCLUDE_WORDS):
            continue  # 금지어가 하나라도 있으면 이 기사는 버리고 다음으로 넘어감
        # ▲▲▲ 여기까지 ▲▲▲

        row = extract_row(item)
        
        # 기사 발행일이 타겟 날짜(target_date)와 다르면 건너뛰기
        if row[COL_DATE] != target_date:
            continue
            
        key = _row_key(row)
        if key not in existing_keys:
            new_rows.append(row)

    # 4) 시트 저장
    append_rows(sheet, new_rows)
    logger.info("✅ 뉴스 수집 완료 (%d개 신규)", len(new_rows))


if __name__ == "__main__":
    main()
