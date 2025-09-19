# 🌧️ 여행 계획 AI API 가이드

프론트엔드 개발자를 위한 완전한 API 사용법입니다.

## 📋 목차

1. [기본 설정](#기본-설정)
2. [여행 계획 생성](#1-여행-계획-생성)
3. [LLM 채팅 인터페이스 (권장)](#2-llm-채팅-인터페이스-권장)
4. [개별 API 엔드포인트](#3-개별-api-엔드포인트)
5. [에러 처리](#에러-처리)
6. [실제 사용 예시](#실제-사용-예시)

---

## 기본 설정

**Base URL**: `http://localhost:8000` (개발환경)

**Content-Type**: `application/json`

**필수 헤더**:
```javascript
{
  "Content-Type": "application/json"
}
```

---

## 1. 여행 계획 생성

### `POST /plan`

기본 여행 계획을 생성합니다. 모든 장소에 **주차장 정보 3개씩** 자동 포함됩니다.

**요청:**
```javascript
const response = await fetch('http://localhost:8000/plan', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
  },
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
});

const plan = await response.json();
```

**응답 예시:**
```javascript
{
  "itinerary": [
    {
      "index": 1,
      "type": "place",
      "title": "강릉역",
      "start_time": "2025-09-20T10:00:00+09:00",
      "end_time": "2025-09-20T10:30:00+09:00",
      "description": "여행 시작 위치",
      "place_id": "ChIJ5fJnlUflYTURWudXtcTihfU",
      "address": "대한민국 강원특별자치도 강릉시 용지로 176",
      "lat": 37.7644776,
      "lng": 128.8995536,
      "rating": 4.5,
      "parking_candidates": [
        {
          "name": "강원특별자치도 강릉시청 교통과",
          "address": "강원특별자치도 강릉시 교동 118-2",
          "lat": 37.76529983,
          "lng": 128.8976196,
          "distance_km": 0.19
        }
        // ... 2개 더
      ]
    }
    // ... 더 많은 장소들
  ],
  "totals": {
    "estimated_cost_krw": 80000,
    "estimated_travel_time_minutes": 180
  }
}
```

---

## 2. LLM 채팅 인터페이스 (권장)

### `POST /rain/chat` ⭐ **가장 간단하고 강력한 방법**

자연어로 모든 기능을 사용할 수 있는 통합 인터페이스입니다.

**기본 사용법:**
```javascript
async function chatWithAI(sessionId, userMessage, plan = null) {
  const response = await fetch('http://localhost:8000/rain/chat', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      session_id: sessionId,
      user_message: userMessage,
      plan: plan // 처음에만 필요
    })
  });
  
  const result = await response.json();
  return result.response; // 사용자 친화적인 메시지
}
```

### 지원하는 자연어 명령들:

#### 🌧️ **비 오는 날 대안 확인**
```javascript
// 처음 사용시 - 플랜과 함께
const response = await chatWithAI("user123", "비 오는 날 대안 확인해줘", plan);

// 이후 사용시 - 메시지만
const response = await chatWithAI("user123", "날씨 나쁠 때 갈 곳 추천해줘");
```

**응답 예시:**
```
비 오는 날을 대비한 대안을 찾았습니다! 🌧️

📍 2개 장소에 대해 총 6개의 실내 대안을 준비했어요.

🎯 **경포호** 대신:
  1. **경포호(鏡浦湖)** ⭐⭐⭐⭐ 
  2. **상영정** ⭐⭐⭐⭐⭐ (0.4km)
  3. **손성목영화박물관** ⭐⭐⭐⭐ (0.4km)

🎯 **오죽헌** 대신:
  1. **오죽헌시립박물관** ⭐⭐⭐⭐ (0.1km)
  2. **강릉화폐전시관** ⭐⭐⭐⭐ (0.1km)
  3. **김시습기념관** ⭐⭐⭐⭐ (0.2km)

'박물관으로 바꿔줘', '두 번째 대안으로 해줘' 등으로 말씀해주시면 적용해드릴게요! 😊
```

#### ✅ **계획 변경**
```javascript
// 구체적인 장소명으로
await chatWithAI("user123", "손성목영화박물관으로 바꿔줘");

// 순서로
await chatWithAI("user123", "경포호 두 번째 대안으로 해줘");

// 일반적으로
await chatWithAI("user123", "박물관으로 바꿔줘");
```

#### 🔄 **되돌리기/초기화**
```javascript
// 한 단계 되돌리기
await chatWithAI("user123", "이전으로 되돌려줘");

// 완전 초기화
await chatWithAI("user123", "처음으로 초기화해줘");
```

#### 📋 **정보 확인**
```javascript
// 현재 계획
await chatWithAI("user123", "현재 계획 보여줘");

// 변경 기록
await chatWithAI("user123", "히스토리 보여줘");

// 도움말
await chatWithAI("user123", "뭘 할 수 있어?");
```

### 완전한 채팅 인터페이스 예시:

```javascript
class TravelPlanChat {
  constructor(baseUrl = 'http://localhost:8000') {
    this.baseUrl = baseUrl;
    this.sessionId = `session_${Date.now()}`;
  }

  async sendMessage(message, plan = null) {
    try {
      const response = await fetch(`${this.baseUrl}/rain/chat`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          session_id: this.sessionId,
          user_message: message,
          plan: plan
        })
      });

      const result = await response.json();
      
      return {
        message: result.response,
        action: result.action,
        success: result.success,
        data: result.data
      };
    } catch (error) {
      return {
        message: "네트워크 오류가 발생했습니다 😅",
        action: "error",
        success: false,
        error: error.message
      };
    }
  }

  // 사용 예시
  async startWithPlan(plan) {
    return await this.sendMessage("비 오는 날 대안 확인해줘", plan);
  }

  async applyChange(message) {
    return await this.sendMessage(message);
  }

  async rollback() {
    return await this.sendMessage("이전으로 되돌려줘");
  }

  async reset() {
    return await this.sendMessage("처음으로 초기화해줘");
  }

  async getCurrentPlan() {
    return await this.sendMessage("현재 계획 보여줘");
  }
}

// 사용법
const chat = new TravelPlanChat();

// 1. 플랜으로 시작
const result1 = await chat.startWithPlan(originalPlan);
console.log(result1.message); // 대안들이 표시됨

// 2. 변경 적용
const result2 = await chat.applyChange("손성목영화박물관으로 바꿔줘");
console.log(result2.message); // "계획을 변경했습니다! ✅"

// 3. 되돌리기
const result3 = await chat.rollback();
console.log(result3.message); // "이전 상태로 되돌렸습니다! 🔄"
```

---

## 3. 개별 API 엔드포인트

세밀한 제어가 필요한 경우 개별 엔드포인트를 사용하세요.

### `POST /rain/check`

비 오는 날 대안을 확인합니다.

```javascript
const response = await fetch('http://localhost:8000/rain/check', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({
    session_id: "user123",
    plan: plan, // /plan에서 받은 계획
    nx: 92,     // 날씨 좌표 (강릉)
    ny: 131,
    protect_titles: ["강릉역"], // 변경하지 않을 장소들
    top_n_parking: 3
  })
});

const result = await response.json();
```

### `POST /rain/apply-choice`

특정 대안을 정확히 선택합니다.

```javascript
const response = await fetch('http://localhost:8000/rain/apply-choice', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({
    session_id: "user123",
    candidate_index: 0,    // 첫 번째 후보 (0부터 시작)
    alternative_index: 2   // 세 번째 대안 (0부터 시작)
  })
});

const result = await response.json();
```

### `POST /rain/llm-apply`

자연어로 대안을 적용합니다.

```javascript
const response = await fetch('http://localhost:8000/rain/llm-apply', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({
    session_id: "user123",
    user_message: "박물관으로 바꿔주세요"
  })
});

const result = await response.json();
```

### `POST /rain/rollback`

이전 상태로 되돌립니다.

```javascript
const response = await fetch('http://localhost:8000/rain/rollback', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({
    session_id: "user123"
  })
});

const result = await response.json();
```

### `POST /rain/reset`

원본 계획으로 완전히 초기화합니다.

```javascript
const response = await fetch('http://localhost:8000/rain/reset', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({
    session_id: "user123"
  })
});

const result = await response.json();
```

### `GET /rain/history/{session_id}`

세션의 변경 기록을 조회합니다.

```javascript
const response = await fetch(`http://localhost:8000/rain/history/user123`);
const history = await response.json();
```

### `POST /weather/summary`

날씨 정보를 직접 조회합니다.

```javascript
const response = await fetch('http://localhost:8000/weather/summary', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({
    nx: 92,  // 강릉 좌표
    ny: 131
  })
});

const weather = await response.json();
```

---

## 에러 처리

### HTTP 상태 코드
- `200`: 성공
- `400`: 잘못된 요청 (필수 파라미터 누락 등)
- `404`: 리소스 없음 (세션 없음 등)
- `500`: 서버 오류

### 에러 응답 예시
```javascript
{
  "detail": "session not found"
}
```

### 프론트엔드 에러 처리 예시
```javascript
async function safeApiCall(apiCall) {
  try {
    const response = await apiCall();
    
    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || '알 수 없는 오류');
    }
    
    return await response.json();
  } catch (error) {
    console.error('API 오류:', error);
    return {
      error: true,
      message: error.message
    };
  }
}
```

---

## 실제 사용 예시

### React 컴포넌트 예시

```jsx
import React, { useState } from 'react';

function TravelPlanAssistant({ originalPlan }) {
  const [sessionId] = useState(`session_${Date.now()}`);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);

  const sendMessage = async (message, plan = null) => {
    setLoading(true);
    
    // 사용자 메시지 추가
    setMessages(prev => [...prev, { type: 'user', content: message }]);

    try {
      const response = await fetch('http://localhost:8000/rain/chat', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          session_id: sessionId,
          user_message: message,
          plan: plan
        })
      });

      const result = await response.json();
      
      // AI 응답 추가
      setMessages(prev => [...prev, { 
        type: 'ai', 
        content: result.response,
        action: result.action,
        data: result.data
      }]);

    } catch (error) {
      setMessages(prev => [...prev, { 
        type: 'error', 
        content: '오류가 발생했습니다: ' + error.message 
      }]);
    } finally {
      setLoading(false);
    }
  };

  const handleSubmit = (e) => {
    e.preventDefault();
    if (input.trim()) {
      sendMessage(input);
      setInput('');
    }
  };

  const startWeatherCheck = () => {
    sendMessage('비 오는 날 대안 확인해줘', originalPlan);
  };

  return (
    <div className="travel-assistant">
      <div className="messages">
        {messages.map((msg, idx) => (
          <div key={idx} className={`message ${msg.type}`}>
            <pre>{msg.content}</pre>
          </div>
        ))}
        {loading && <div className="loading">AI가 답변을 준비중입니다...</div>}
      </div>

      <div className="quick-actions">
        <button onClick={startWeatherCheck}>
          🌧️ 비 오는 날 대안 확인
        </button>
        <button onClick={() => sendMessage('현재 계획 보여줘')}>
          📋 현재 계획
        </button>
        <button onClick={() => sendMessage('이전으로 되돌려줘')}>
          🔄 되돌리기
        </button>
      </div>

      <form onSubmit={handleSubmit} className="input-form">
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="자연어로 말씀해주세요... (예: 박물관으로 바꿔줘)"
          disabled={loading}
        />
        <button type="submit" disabled={loading || !input.trim()}>
          전송
        </button>
      </form>
    </div>
  );
}

export default TravelPlanAssistant;
```

### Vue.js 컴포넌트 예시

```vue
<template>
  <div class="travel-assistant">
    <div class="messages">
      <div 
        v-for="(msg, idx) in messages" 
        :key="idx" 
        :class="`message ${msg.type}`"
      >
        <pre>{{ msg.content }}</pre>
      </div>
      <div v-if="loading" class="loading">
        AI가 답변을 준비중입니다...
      </div>
    </div>

    <div class="quick-actions">
      <button @click="startWeatherCheck">
        🌧️ 비 오는 날 대안 확인
      </button>
      <button @click="sendMessage('현재 계획 보여줘')">
        📋 현재 계획
      </button>
      <button @click="sendMessage('이전으로 되돌려줘')">
        🔄 되돌리기
      </button>
    </div>

    <form @submit.prevent="handleSubmit" class="input-form">
      <input
        v-model="input"
        type="text"
        placeholder="자연어로 말씀해주세요... (예: 박물관으로 바꿔줘)"
        :disabled="loading"
      />
      <button type="submit" :disabled="loading || !input.trim()">
        전송
      </button>
    </form>
  </div>
</template>

<script>
export default {
  name: 'TravelPlanAssistant',
  props: {
    originalPlan: Object
  },
  data() {
    return {
      sessionId: `session_${Date.now()}`,
      messages: [],
      input: '',
      loading: false
    };
  },
  methods: {
    async sendMessage(message, plan = null) {
      this.loading = true;
      
      this.messages.push({ type: 'user', content: message });

      try {
        const response = await fetch('http://localhost:8000/rain/chat', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            session_id: this.sessionId,
            user_message: message,
            plan: plan
          })
        });

        const result = await response.json();
        
        this.messages.push({ 
          type: 'ai', 
          content: result.response,
          action: result.action,
          data: result.data
        });

      } catch (error) {
        this.messages.push({ 
          type: 'error', 
          content: '오류가 발생했습니다: ' + error.message 
        });
      } finally {
        this.loading = false;
      }
    },

    handleSubmit() {
      if (this.input.trim()) {
        this.sendMessage(this.input);
        this.input = '';
      }
    },

    startWeatherCheck() {
      this.sendMessage('비 오는 날 대안 확인해줘', this.originalPlan);
    }
  }
};
</script>
```

---

## 💡 프론트엔드 개발 팁

### 1. 세션 관리
- 각 사용자마다 고유한 `session_id` 생성
- 브라우저 새로고침 시에도 세션 유지 원한다면 localStorage 활용

```javascript
const getSessionId = () => {
  let sessionId = localStorage.getItem('travel_session_id');
  if (!sessionId) {
    sessionId = `session_${Date.now()}_${Math.random()}`;
    localStorage.setItem('travel_session_id', sessionId);
  }
  return sessionId;
};
```

### 2. 로딩 상태 관리
- API 호출 중 사용자에게 로딩 표시
- 중복 요청 방지

### 3. 에러 처리
- 네트워크 오류, API 오류 모두 처리
- 사용자 친화적인 에러 메시지 표시

### 4. 반응형 메시지
- `/rain/chat`의 응답은 이미 사용자 친화적
- 그대로 표시하거나 파싱해서 UI에 맞게 조정

### 5. 데이터 캐싱
- 플랜 정보는 상태 관리 라이브러리에 저장
- 불필요한 API 호출 최소화

---

## 🚀 시작하기

1. **기본 플랜 생성**
   ```javascript
   const plan = await createPlan(planData);
   ```

2. **LLM 채팅 시작**
   ```javascript
   const chat = new TravelPlanChat();
   const result = await chat.startWithPlan(plan);
   ```

3. **사용자와 대화**
   ```javascript
   const response = await chat.sendMessage(userInput);
   ```

**이게 전부입니다!** 🎉

더 궁금한 점이 있으시면 언제든 문의해주세요! 😊
