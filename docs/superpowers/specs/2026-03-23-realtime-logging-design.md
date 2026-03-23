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
| 이름 (title) | `"HH:MM \| 메시지 텍스트"` | `"14:30 \| 카페에서 기획서 작성했어"` |
| 시작 시간 (text) | `"HH:MM"` (KST) | `"14:30"` |
| 종료 시간 (text) | 비워둠 | |
| 집중 시간 (number) | 비워둠 | |
| 메모 (text) | 원문 메시지 | `"카페에서 기획서 작성했어"` |
| 날짜(Daily Log 연결) (date) | 해당 날짜 | `"2026-03-23"` |
| Daily Log DB (relation) | 메인 페이지 ID | |

### 메인DB 페이지

- 그날 첫 메시지 시 자동 생성 (기존과 동일한 속성)
- "일기" 필드: 오후 5시에 AI 요약으로 채워짐
- children 블록: 사용하지 않음 (상세DB에서 관리)

### 오후 5시 동작

1. 기존처럼 질문 메시지 발송
2. **추가**: 그날 상세DB 기록 수집 → AI 요약 생성 → 메인DB "일기" 필드 업데이트
3. 요약 결과를 텔레그램으로도 전송

### 텔레그램 응답

- 기록 시: `"✅ 14:30 기록 완료!"` (즉시, AI 없음)
- 오후 5시: 질문 + 하루 요약 결과

## 코드 변경 범위

`daily_journal_bot.py` 단일 파일 수정:

1. **handle_message()**: AI 요약 제거, 상세DB에 행 추가, 메인DB 페이지 생성/연결
2. **create_notion_page()**: children 블록 제거, 메인 페이지만 생성 (relation용)
3. **append_to_notion()**: 삭제 (더 이상 메인DB에 직접 append 안 함)
4. **create_detail_record()**: 새 함수 — 상세DB에 행 추가
5. **send_daily_question()**: 질문 발송 후 하루 기록 수집 → AI 요약 → 메인DB 업데이트
6. **get_today_records()**: 새 함수 — 상세DB에서 오늘 기록 조회 (Notion API query)
7. **상태 관리**: today_messages 배열 제거 (상세DB가 source of truth)

## Notion API 사용

- 상세DB 행 추가: `POST /v1/pages` (parent: 상세DB ID)
- 메인DB 페이지 생성: `POST /v1/pages` (parent: 메인DB ID) — 기존과 동일
- 상세DB 조회: `POST /v1/databases/{상세DB_ID}/query` (날짜 필터)
- 메인DB 업데이트: `PATCH /v1/pages/{page_id}` — 일기 속성 갱신

## 상세DB ID

config.env에 `NOTION_DETAIL_DATABASE_ID` 추가 필요. 값: `1c02a4276103802583e1d12c6eb26558`

## 에러 처리

- 상세DB 기록 실패 시: 에러 로그 + 텔레그램에 실패 알림
- 메인DB 생성 실패 시: 상세DB 기록은 진행, relation만 비워둠
- AI 요약 실패 시: 기존과 동일하게 실패 메시지 저장
