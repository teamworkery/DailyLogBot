# DailyLogBot: 수시 기록 방식 전환 설계

## 배경

사용자가 하루 중 수시로 텔레그램에 활동 기록을 보내는 방식으로 전환한다. 기존에는 오후 5시 질문에 대한 답변만 처리했으나, 이제는 언제든 메시지를 보내면 즉시 기록되어야 한다.

핵심 원칙: **순수 기록에 초점** — 빠른 기록, 즉시 확인, AI 요약은 하루 한 번.

## 현재 구조

- 메시지 → AI 요약(매번) → 메인 DB(Daily Log DB)에 직접 기록
- 매 메시지마다 전체 재요약 → 느리고 비효율적
- 시간 정보 없음

## 변경 후 구조

### 데이터 흐름

```
사용자 메시지
  → 상세DB(Daily Log 상세DB)에 즉시 행 추가
  → 메인DB(Daily Log DB) 페이지와 relation 연결
  → 즉시 확인 응답 (AI 호출 없음)

오후 5시
  → 일일 질문 발송
  → 그날 상세DB 기록 전체 수집
  → AI 요약 생성
  → 메인DB "일기" 필드에 요약 저장
```

### 상세DB 기록 형식

| 필드 | 값 | 예시 |
|------|------|------|
| 이름 (title) | `"HH:MM \| 메시지 텍스트"` (200자 초과 시 truncate) | `"14:30 \| 카페에서 기획서 작성했어"` |
| 시작 시간 (text) | `"HH:MM"` (KST) | `"14:30"` |
| 종료 시간 (text) | 비워둠 | |
| 집중 시간 (number) | 비워둠 | |
| 메모 (text) | 원문 메시지 전체 | `"카페에서 기획서 작성했어"` |
| 날짜(Daily Log 연결) (date) | 해당 날짜 | `"2026-03-23"` |
| Daily Log DB (relation) | 메인 페이지와 연결 | |

Notion relation 속성 API 형식:
```json
"Daily Log DB": {
  "relation": [{ "id": "<main_page_id>" }]
}
```

### 메인DB 페이지

- 그날 첫 메시지 시 자동 생성 (기존과 동일한 속성)
- "일기" 필드: 오후 5시에 AI 요약으로 채워짐
- children 블록: 사용하지 않음 (상세DB에서 관리)

### 오후 5시 동작

1. 질문 메시지 발송 (기록 유무와 무관하게 항상 발송, 기존 skip 로직 제거)
2. 그날 상세DB 기록 수집 → 기록이 있으면 AI 요약 생성 → 메인DB "일기" 필드 업데이트
3. 기록이 없으면 요약 생략, 질문만 발송
4. 요약 결과를 텔레그램으로도 전송
5. 질문 발송 후 들어오는 답변도 일반 기록과 동일하게 처리 (상세DB에 추가)

### 텔레그램 응답

- 기록 시: `"✅ 14:30 기록 완료!"` (즉시, AI 없음)
- 오후 5시: 질문 + 하루 요약 결과 (기록이 있는 경우)

## 상태 관리

`bot_state.json` 새 구조:
```json
{
  "last_question_date": "2026-03-23",
  "today_page_id": "notion-page-uuid",
  "today_page_date": "2026-03-23"
}
```

- `today_messages` 배열 제거 — 상세DB가 source of truth
- `today_page_id`, `today_page_date` 유지 — 메인 페이지 중복 생성 방지
- `last_question_date` 유지 — 질문 중복 발송 방지

## 코드 변경 범위

`daily_journal_bot.py` 단일 파일 수정:

1. **handle_message()**: AI 요약 제거, 상세DB에 행 추가, 메인DB 페이지 생성/연결
2. **create_notion_page()**: children 블록 제거, 메인 페이지만 생성 (relation용)
3. **append_to_notion()**: 삭제 (더 이상 메인DB에 직접 append 안 함)
4. **create_detail_record()**: 새 함수 — 상세DB에 행 추가 + relation 연결
5. **send_daily_question()**: 항상 질문 발송 + 하루 기록 수집 → AI 요약 → 메인DB 업데이트
6. **get_today_records()**: 새 함수 — 상세DB에서 오늘 기록 조회
7. **summarize_with_anthropic()**: 프롬프트 수정 — 타임스탬프가 포함된 여러 기록 입력 처리

## Notion API 사용

상세DB 행 추가:
```
POST /v1/pages
{
  "parent": { "database_id": "<DETAIL_DB_ID>" },
  "properties": {
    "이름": { "title": [{ "text": { "content": "14:30 | 메시지" } }] },
    "시작 시간": { "rich_text": [{ "text": { "content": "14:30" } }] },
    "메모": { "rich_text": [{ "text": { "content": "원문 메시지" } }] },
    "날짜(Daily Log 연결)": { "date": { "start": "2026-03-23" } },
    "Daily Log DB": { "relation": [{ "id": "<page_id>" }] }
  }
}
```

상세DB 오늘 기록 조회:
```
POST /v1/databases/<DETAIL_DB_ID>/query
{
  "filter": {
    "property": "날짜(Daily Log 연결)",
    "date": { "equals": "2026-03-23" }
  },
  "sorts": [{ "property": "시작 시간", "direction": "ascending" }]
}
```

메인DB 페이지 생성: `POST /v1/pages` (parent: 메인DB ID) — 기존과 유사, children 제거

메인DB 업데이트: `PATCH /v1/pages/{page_id}` — 일기 속성 갱신

## 환경 변수

config.env에 `NOTION_DETAIL_DATABASE_ID` 추가 필요.

## AI 요약 프롬프트

기존: 단일 텍스트 blob 입력
변경: 타임스탬프가 포함된 여러 기록 형태의 입력

```
다음은 {date_str}의 활동 기록입니다.
한국어로 핵심 내용과 주요 활동을 2-4개의 bullet point로 간결하게 요약해주세요:

14:30 | 카페에서 기획서 작성했어
15:45 | 팀 미팅 참석
17:20 | 코드 리뷰 진행
```

## 에러 처리

- 상세DB 기록 실패 시: 에러 로그 + 텔레그램에 실패 알림
- 메인DB 생성 실패 시: 상세DB 기록은 진행, relation만 비워둠. 다음 메시지에서 메인 페이지 재생성 시도.
- AI 요약 실패 시: 기존과 동일하게 실패 메시지 저장
- 5PM 요약 중 동시 메시지: 상세DB가 source of truth이므로 누락된 기록은 다음 요약 시 반영됨 (허용 가능한 수준)
