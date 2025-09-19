# 🌧️ 여행 계획 AI API

자연어로 소통하는 지능형 여행 계획 관리 시스템입니다. 비 오는 날을 대비한 실내 대안을 자동으로 제안하고, 주차장 정보까지 포함된 완전한 여행 계획을 제공합니다.

## ✨ 주요 기능

### 🤖 자연어 대화 인터페이스
- **"비 오는 날 대안 확인해줘"** → 날씨 기반 실내 대안 자동 제안
- **"박물관으로 바꿔줘"** → 자연어로 계획 변경
- **"이전으로 되돌려줘"** → 변경사항 롤백
- **"현재 계획 보여줘"** → 현재 상태 확인

### 🌧️ 스마트 날씨 대응
- 실시간 날씨 정보 기반 대안 제안
- 실내 장소 우선 추천 (박물관, 카페, 갤러리 등)
- 평점과 거리 정보 포함

### 🅿️ 통합 주차장 정보
- 각 장소마다 **주차장 3곳** 자동 추천
- 거리순 정렬로 편리한 주차 계획

### 🔄 완전한 상태 관리
- 변경 기록 추적 (History)
- 단계별 롤백 기능
- 원본 계획으로 완전 초기화

## 🚀 빠른 시작

### 1. 저장소 클론
```bash
git clone https://github.com/lol-chang/pln.git
cd pln
```

### 2. 가상환경 설정
```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
```

### 3. 패키지 설치
```bash
pip install -r requirements.txt
```

### 4. 환경 변수 설정
```bash
cp env.example .env
# .env 파일을 열어서 실제 API 키 입력
```

**필수 API 키:**
- **Google Places API**: [발급받기](https://developers.google.com/maps/documentation/places/web-service/get-api-key)
- **OpenAI API**: [발급받기](https://platform.openai.com/api-keys)

### 5. 서버 실행
```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

서버가 실행되면 http://localhost:8000 에서 접근 가능합니다.

## 📖 API 사용법

### 🎯 추천: LLM 채팅 인터페이스 사용

가장 간단하고 강력한 방법입니다:

```javascript
// 1. 여행 계획 생성
const plan = await fetch('http://localhost:8000/plan', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    fest_title: "강릉역",
    fest_location_text: "강원 강릉시 용지로 176",
    travel_needs: {
      start_at: "2025-09-20T10:00:00+09:00",
      end_at: "2025-09-20T18:00:00+09:00",
      categories: ["관광", "카페", "맛집"],
      budget: 100000
    }
  })
}).then(r => r.json());

// 2. 자연어로 대화
const chat = async (message, plan = null) => {
  const response = await fetch('http://localhost:8000/rain/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      session_id: "user123",
      user_message: message,
      plan: plan
    })
  });
  return response.json();
};

// 사용 예시
await chat("비 오는 날 대안 확인해줘", plan);
// → "비 오는 날을 대비한 대안을 찾았습니다! 🌧️..."

await chat("손성목영화박물관으로 바꿔줘");
// → "계획을 변경했습니다! ✅..."

await chat("이전으로 되돌려줘");
// → "이전 상태로 되돌렸습니다! 🔄..."
```

### 📚 상세한 API 문서

완전한 API 가이드는 [`API_GUIDE.md`](./API_GUIDE.md)를 참고하세요:
- React/Vue.js 컴포넌트 예시
- 모든 엔드포인트 상세 설명
- 에러 처리 방법
- 실제 사용 예시

## 🏗️ 프로젝트 구조

```
pln/
├── main.py                     # FastAPI 애플리케이션
├── requirements.txt            # Python 패키지 목록
├── env.example                 # 환경 변수 예시
├── API_GUIDE.md               # 프론트엔드 개발자용 가이드
│
├── google_places_singleton.py  # Google Places API 클라이언트
├── openai_singleton.py        # OpenAI API 클라이언트
├── planner_singleton.py       # 여행 계획 생성 로직
├── rain_change_proposal.py    # 비 오는 날 대안 제안
├── scheduler_module.py        # 날씨 스케줄러
├── llm.py                     # LLM 관련 함수들
├── request_models.py          # API 요청 모델들
│
├── cloud_function/            # 날씨 크롤링 Cloud Function
│   ├── main.py
│   ├── requirements.txt
│   └── README.md
│
└── 강원특별자치도_강릉시_주차장정보_20230828.csv  # 주차장 데이터
```

## 🔧 환경 변수

| 변수명 | 필수 | 설명 |
|--------|------|------|
| `GOOGLE_API_KEY` | ✅ | Google Places API 키 |
| `OPENAI_API_KEY` | ✅ | OpenAI API 키 |
| `FUNCTION_URL` | ❌ | 날씨 크롤링 Cloud Function URL |
| `FUNCTION_AUDIENCE` | ❌ | Cloud Function 인증 정보 |
| `DEFAULT_NX` | ❌ | 기본 날씨 좌표 X (기본값: 92) |
| `DEFAULT_NY` | ❌ | 기본 날씨 좌표 Y (기본값: 131) |
| `ENABLE_PARKING_INFO` | ❌ | 주차장 정보 활성화 (기본값: true) |

## 🎮 사용 예시

### 1. 기본 여행 계획 생성
```bash
curl -X POST "http://localhost:8000/plan" \
  -H "Content-Type: application/json" \
  -d '{
    "fest_title": "강릉역",
    "fest_location_text": "강원 강릉시 용지로 176",
    "travel_needs": {
      "start_at": "2025-09-20T10:00:00+09:00",
      "end_at": "2025-09-20T18:00:00+09:00",
      "categories": ["관광", "카페", "맛집"],
      "budget": 100000
    }
  }'
```

### 2. 자연어 대화
```bash
# 비 오는 날 대안 확인
curl -X POST "http://localhost:8000/rain/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "demo123",
    "user_message": "비 오는 날 대안 확인해줘",
    "plan": {...}
  }'

# 계획 변경
curl -X POST "http://localhost:8000/rain/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "demo123",
    "user_message": "박물관으로 바꿔줘"
  }'

# 되돌리기
curl -X POST "http://localhost:8000/rain/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "demo123",
    "user_message": "이전으로 되돌려줘"
  }'
```

## 🌟 특징

- **🤖 GPT-4o-mini 기반** 자연어 처리
- **🌧️ 실시간 날씨** 기반 대안 제안
- **🅿️ 주차장 정보** 자동 통합 (각 장소마다 3곳)
- **⭐ 평점과 거리** 정보 포함
- **🔄 완전한 상태 관리** (History, Rollback, Reset)
- **📱 프론트엔드 친화적** API 설계
- **🚀 FastAPI** 기반 고성능 서버

## 🤝 기여하기

1. Fork the Project
2. Create your Feature Branch (`git checkout -b feature/AmazingFeature`)
3. Commit your Changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the Branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

## 📄 라이센스

This project is licensed under the MIT License.

## 📞 문의

프로젝트에 대한 질문이나 제안사항이 있으시면 언제든 연락주세요!

---

**🌧️ 비 오는 날도 완벽한 여행을 즐기세요!** ✨