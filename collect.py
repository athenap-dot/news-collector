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
from datetime import datetime, timedelta
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import gspread

# ── 설정 ──────────────────────────────────────────────
LOGGING_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOGGING_FORMAT)
logger = logging.getLogger(__name__)

KEYWORDS = ["키움DRX", "DRX", "KRX", "디알엑스", "키움디알엑스"]

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
COL_DATE = 0       # 수집일자
COL_PUBDATE = 1    # 기사 발행일
COL_TITLE = 2      # 뉴스 제목
COL_MEDIA = 3      # 언론사
COL_URL = 4        # 기사 링크


def _date_key() -> str:
    """오늘 날짜 문자열 (YYYY-MM-DD)."""
    return datetime.now().strftime("%Y-%m-%d")


def _row_key(row: list[str]) -> str:
    """기사 고유 키 (발행일+제목) → 중복 감지용."""
    return hashlib.sha256(
        "|".join(row[COL_PUBDATE:COL_PUBDATE + 3]).encode()
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
def normalize_date(raw: str) -> str:
    """YYYYMMDD → YYYY-MM-DD (예외는 원본 유지)."""
    try:
        d = datetime.strptime(raw, "%Y%m%d")
        return d.strftime("%Y-%m-%d")
    except ValueError:
        return raw


def extract_row(item):
    # HTML 태그 제거 및 깔끔한 텍스트로 변환 (기존 코드에 있다면 유지)
    title = item.get("title", "").replace("<b>", "").replace("</b>", "").replace("&quot;", "\"")
    
    return [
        "e-Sports",                       # Category (임의의 고정값 지정)
        normalize_date(item["pubDate"]),  # Date (발행일)
        title,                            # Title (기사 제목 - 추천!)
        "",                               # Media Name (네이버 API 미제공으로 빈칸)
        "Korean",                         # Language (고정값)
        item["link"]                      # URL (기사 링크)
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

    # 3) 중복 필터링
    new_rows = []
    for item in items:
        row = extract_row(item)
        key = _row_key(row)
        if key not in existing_keys:
            new_rows.append(row)

    if not new_rows:
        logger.info("중복 기사만 발견 → 새 데이터 없음")
        return

    # 4) 시트 저장
    append_rows(sheet, new_rows)
    logger.info("✅ 뉴스 수집 완료 (%d개 신규)", len(new_rows))


if __name__ == "__main__":
    main()
