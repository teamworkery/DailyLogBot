# 📓 Daily Journal Bot

매일 오후 5시에 Telegram으로 일기 질문을 보내고, 답변을 AI로 요약해서 Notion에 자동 저장합니다.

---

## ⚡ 빠른 시작

### 1단계: Python 설치 확인
```
python --version  # 3.10 이상 권장
```

### 2단계: 패키지 설치
```bash
cd C:\Users\swm09\Desktop\Cowork\DailyLogBot
pip install -r requirements.txt
```

### 3단계: API 키 설정
`config.env` 파일을 열어서 아래 두 항목을 채워주세요:

| 항목 | 발급 주소 |
|------|-----------|
| `ANTHROPIC_API_KEY` | https://console.anthropic.com |
| `NOTION_TOKEN` | https://www.notion.so/my-integrations |

> ⚠️ **Notion 설정 추가 필요**: Integration 만든 후 **Daily Log DB** 데이터베이스에 해당 Integration을 연결해야 합니다.
> DB 페이지 → 우상단 `...` → `Connections` → Integration 추가

### 4단계: 봇 실행
```bash
python daily_journal_bot.py
```

창이 열린 채로 두면 매일 오후 5시에 자동으로 질문이 발송됩니다.

---

## 🔄 백그라운드 자동 실행 (컴퓨터 켤 때마다 자동 시작)

### Windows (작업 스케줄러)
1. `시작` → `작업 스케줄러` 검색 → 열기
2. 오른쪽 `작업 만들기` 클릭
3. **일반** 탭: 이름 → `DailyJournalBot`
4. **트리거** 탭: `새로 만들기` → `로그온 시` 선택
5. **동작** 탭: `새로 만들기`
   - 프로그램: `python`
   - 인수: `C:\Users\swm09\Desktop\Cowork\DailyLogBot\daily_journal_bot.py`
   - 시작 위치: `C:\Users\swm09\Desktop\Cowork\DailyLogBot`
6. 확인 저장

---

## 📁 파일 구조

```
DailyLogBot/
├── daily_journal_bot.py   # 메인 봇 스크립트
├── requirements.txt        # Python 패키지 목록
├── config.env              # API 키 및 설정
├── bot_state.json          # 실행 상태 저장 (자동 생성)
├── bot.log                 # 실행 로그 (자동 생성)
└── README.md               # 이 파일
```

---

## 🔧 동작 방식

```
[매일 오후 5시]
       ↓
  Telegram 질문 발송
  "오늘은 무슨 일을 하셨나요?"
       ↓
  사용자 답변 수신
       ↓
  Claude AI (Haiku) 요약 생성
       ↓
  Notion Daily Log DB에 페이지 생성
  - 제목: 날짜 (예: 2026-02-27)
  - 내용: 원문 + AI 요약
       ↓
  Telegram으로 저장 완료 알림
```

---

## ❓ 자주 묻는 질문

**Q: 질문 시간을 바꾸고 싶어요**
A: `config.env`에서 `QUESTION_HOUR`와 `QUESTION_MINUTE` 수정 후 재시작

**Q: 컴퓨터가 꺼져 있으면 어떻게 되나요?**
A: 해당 날 질문이 발송되지 않습니다. 항상 실행이 필요하면 클라우드 서버(Oracle Free Tier 등) 사용을 권장합니다.

**Q: 로그는 어디서 보나요?**
A: `bot.log` 파일에 모든 실행 기록이 저장됩니다.
