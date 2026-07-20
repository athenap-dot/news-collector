# 📰 뉴스 자동 수집 & 구글 시트 자동화

특정 키워드가 포함된 당일 뉴스를 자동으로 수집해 Google Sheets에 저장합니다.  
GitHub Actions에서 매일 오전 8시(KST) 자동 실행됩니다.

## 🔑 사전 준비

### 1. 네이버 Open API 발급 (무료)

1. [Naver Developer](https://developers.naver.com/) 에 로그인
2. **내 애플리케이션** → **애플리케이션 추가**
3. 서비스 이름 예: `news-collector`, 카테고리: `기타`
4. 발급된 **Client ID** 와 **Client Secret** 을 복사

### 2. Google 서비스 계정 & 시트 권한

#### 2-1. 서비스 계정 만들기

1. [Google Cloud Console](https://console.cloud.google.com/) → 프로젝트 생성 (또는 기존 선택)
2. **IAM 및 관리자** → **서비스 계정** → **서비스 계정 만들기**
   - 이름: `news-collector`, 설명: `자동 뉴스 수집용`
3. 생성 후 **인스턴스 세부정보** → **키** 탭 → **새 키 만들기** → **JSON** 선택
4. 다운로드된 JSON 파일을 열어 내용 전체를 복사 (아래 2-3 에 사용)

#### 2-2. Google Sheets API 활성화

1. Google Cloud Console → **API 및 서비스** → **사용 가능한 API**
2. **Google Sheets API** 검색 → **활성화**

#### 2-3. 시트에 서비스 계정 공유

1. Google Sheets에서 수집할 시트 열기
2. 오른쪽 위 **공유** 버튼 → 서비스 계정 이메일 (JSON 파일의 `"client_email"` 값) 입력
3. 권한: **편집자** → **공유**

### 3. GitHub Secrets 설정

1. 이 리포지토리 페이지 → **Settings** → **Secrets and variables** → **Actions**
2. 아래 5개를 각각 추가:

| Secret 이름 | 값 |
|---|---|
| `NAVER_CLIENT_ID` | 네이버 API Client ID |
| `NAVER_CLIENT_SECRET` | 네이버 API Client Secret |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | 2-1 에서 다운로드한 JSON 파일 **전체 내용** |
| `SPREADSHEET_ID` | 시트 URL의 `spreadsheets/d/xxxxx` 중 `xxxxx` 부분 |
| `SHEET_NAME` | 시트 이름 (기본값: `시트1`) |

## ▶️ 로컬에서 테스트

```bash
python -m venv .venv
.venv\Scripts\Activate.psle      # Windows
pip install -r requirements.txt

export NAVER_CLIENT_ID='your-id'
export NAVER_CLIENT_SECRET='your-secret'
export GOOGLE_SERVICE_ACCOUNT_JSON='{"type":"service_account",...}'
export SPREADSHEET_ID='your-sheet-id'

python collect.py
```

## 🏃 실행 흐름

```
뉴스 수집 (키워드별) → 중복 제거 (발행일+제목 해시) → 시트에 행 추가
```

- 같은 제목의 기사는 하루에 한 번만 수집
- 이미 존재하는 기사는 스킵
- 실행 로그는 GitHub Actions 로그에서 확인 가능

## ⚙️ 커스터마이징

- **키워드 수정**: `collect.py` 의 `KEYWORDS` 리스트 변경
- **실행 시간 변경**: `.github/workflows/main.yml` 의 `cron` 필드 수정
  - KST 08:00 = UTC 23:00 전일 (`0 23 * * *`)
  - KST 09:00 = UTC 00:00 (`0 0 * * *`)
- **시트 컬럼 수정**: `collect.py` 의 `COL_*` 상수 및 `ensure_headers()` 함수

## 📄 라이선스

MIT
