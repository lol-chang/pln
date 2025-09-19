# 🌧️ 날씨 기능 설정 가이드

이 문서는 **선택적 날씨 기반 대안 제안 기능**을 활성화하는 방법을 설명합니다.

## ⚠️ 중요 안내

- 날씨 기능은 **선택사항**입니다
- 설정하지 않아도 다른 모든 기능은 정상 작동합니다
- 날씨 기능 없이도 수동으로 대안을 확인할 수 있습니다

## 🌤️ 날씨 기능이 제공하는 것

- **자동 날씨 확인**: "비 오는 날 대안 확인해줘" 명령시 실시간 날씨 체크
- **스마트 대안 제안**: 비가 올 때만 실내 대안 자동 제안
- **예보 기반 계획**: 여행 날짜의 날씨 예보를 고려한 계획 수정

## 🛠️ 설정 방법

### 방법 1: Google Cloud Function 배포 (권장)

#### 1. Cloud Function 코드 준비

`weather-function/main.py` 파일을 생성하세요:

```python
import functions_framework
import requests
import json
from datetime import datetime, timedelta
import os

@functions_framework.http
def crawl_weather(request):
    """한국 기상청 API를 사용한 날씨 크롤링"""
    
    # CORS 설정
    if request.method == 'OPTIONS':
        headers = {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET, POST',
            'Access-Control-Allow-Headers': 'Content-Type',
            'Access-Control-Max-Age': '3600'
        }
        return ('', 204, headers)

    headers = {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Headers': 'Content-Type',
    }

    try:
        # 요청 데이터 파싱
        request_json = request.get_json(silent=True)
        if not request_json:
            return json.dumps({"error": "Invalid JSON"}), 400, headers

        nx = request_json.get('nx', 92)  # 강릉 기본값
        ny = request_json.get('ny', 131)

        # 기상청 API 호출
        service_key = os.environ.get('WEATHER_API_KEY')  # 기상청 API 키
        if not service_key:
            return json.dumps({"error": "Weather API key not configured"}), 500, headers

        base_date = datetime.now().strftime('%Y%m%d')
        base_time = '0500'  # 05:00 기준

        url = 'http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst'
        params = {
            'serviceKey': service_key,
            'pageNo': '1',
            'numOfRows': '1000',
            'dataType': 'JSON',
            'base_date': base_date,
            'base_time': base_time,
            'nx': str(nx),
            'ny': str(ny)
        }

        response = requests.get(url, params=params)
        data = response.json()

        # 데이터 처리 및 비 오는 날 추출
        rainy_dates = []
        if 'response' in data and 'body' in data['response']:
            items = data['response']['body']['items']['item']
            
            for item in items:
                if item['category'] == 'PTY' and int(item['fcstValue']) > 0:
                    # 비가 오는 날짜 추가
                    date = item['fcstDate']
                    formatted_date = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
                    if formatted_date not in rainy_dates:
                        rainy_dates.append(formatted_date)

        result = {
            "rainy_dates": rainy_dates,
            "location": {"nx": nx, "ny": ny},
            "updated_at": datetime.now().isoformat()
        }

        return json.dumps(result, ensure_ascii=False), 200, headers

    except Exception as e:
        error_result = {"error": str(e)}
        return json.dumps(error_result), 500, headers
```

#### 2. requirements.txt 생성

`weather-function/requirements.txt`:

```
functions-framework==3.*
requests==2.*
```

#### 3. Google Cloud Function 배포

```bash
# Google Cloud CLI 설치 및 인증
gcloud auth login
gcloud config set project YOUR_PROJECT_ID

# Cloud Function 배포
gcloud functions deploy crawl-weather \
    --runtime python311 \
    --trigger-http \
    --allow-unauthenticated \
    --set-env-vars WEATHER_API_KEY=YOUR_WEATHER_API_KEY \
    --source ./weather-function
```

#### 4. 환경 변수 설정

배포 완료 후 `.env` 파일에 추가:

```bash
FUNCTION_URL=https://YOUR_REGION-YOUR_PROJECT.cloudfunctions.net/crawl-weather
```

### 방법 2: 다른 날씨 API 서비스 사용

OpenWeatherMap, AccuWeather 등 다른 날씨 API를 사용하려면:

1. `scheduler_module.py`의 `fetch_weather_summary` 함수를 수정
2. 해당 API의 응답 형식에 맞게 데이터 파싱 로직 변경
3. 환경 변수에 새로운 API 키와 URL 설정

### 방법 3: 날씨 기능 비활성화 (기본)

아무 설정도 하지 않으면:

- "비 오는 날 대안 확인해줘" → "날씨 정보를 가져올 수 없어 모든 장소에 대한 대안을 제안합니다"
- 수동으로 `/rain/check` API에 `rainy_dates_input` 파라미터를 제공하여 사용 가능

## 🔧 필요한 API 키

### 한국 기상청 API (무료)

1. [공공데이터포털](https://www.data.go.kr/) 회원가입
2. "기상청_단기예보 ((구)동네예보) 조회서비스" 신청
3. 승인 후 서비스키 발급
4. Cloud Function 환경 변수에 설정

## 🧪 테스트 방법

### 1. Cloud Function 직접 테스트

```bash
curl -X POST "https://YOUR_FUNCTION_URL" \
  -H "Content-Type: application/json" \
  -d '{"nx": 92, "ny": 131}'
```

### 2. API 통합 테스트

```bash
curl -X POST "http://localhost:8000/weather/summary" \
  -H "Content-Type: application/json" \
  -d '{"nx": 92, "ny": 131}'
```

### 3. 채팅 인터페이스 테스트

```bash
curl -X POST "http://localhost:8000/rain/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "test123",
    "user_message": "비 오는 날 대안 확인해줘",
    "plan": {...}
  }'
```

## 🎯 좌표 정보

주요 도시의 기상청 격자 좌표:

| 도시 | NX | NY |
|------|----|----|
| 서울 | 60 | 127 |
| 부산 | 98 | 76 |
| 대구 | 89 | 90 |
| 인천 | 55 | 124 |
| 광주 | 58 | 74 |
| 대전 | 67 | 100 |
| 울산 | 102 | 84 |
| 강릉 | 92 | 131 |
| 제주 | 52 | 38 |

## 💡 팁

- **개발 환경**에서는 날씨 기능 없이 테스트하고, **프로덕션**에서만 활성화하는 것을 권장
- Cloud Function은 **콜드 스타트**로 인해 첫 호출시 지연이 있을 수 있음
- 기상청 API는 **일일 호출 제한**이 있으므로 적절한 캐싱 구현 권장

## 🚨 문제 해결

### "날씨 정보를 가져올 수 없습니다"

1. `FUNCTION_URL` 환경 변수 확인
2. Cloud Function 배포 상태 확인
3. 기상청 API 키 유효성 확인
4. 네트워크 연결 상태 확인

### Cloud Function 오류

1. Google Cloud Console에서 로그 확인
2. 환경 변수 설정 확인
3. API 키 권한 확인

---

**날씨 기능은 선택사항입니다. 설정하지 않아도 여행 계획 AI의 핵심 기능들은 모두 사용할 수 있습니다!** 🌟
