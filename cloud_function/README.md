배포 
gcloud auth login
gcloud config set project <PROJECT_ID>

gcloud services enable cloudfunctions.googleapis.com run.googleapis.com cloudbuild.googleapis.com

gcloud functions deploy crawl-weather \
  --gen2 \
  --runtime python311 \
  --region asia-northeast3 \
  --source . \
  --entry-point crawl_weather \
  --trigger-http \
  --no-allow-unauthenticated \
  --set-env-vars KMA_SERVICE_KEY='<기상청서비스키>',DEFAULT_NX='92',DEFAULT_NY='131'




서버에서 1시간마다 가져오기 (예: FastAPI + APScheduler)
  # server_side_poll.py (예시)
import os, requests, time
from datetime import datetime

FUNCTION_URL = os.getenv("FUNCTION_URL")  # 배포 후 받은 URL
NX, NY = 92, 131

def fetch_and_store():
    r = requests.post(
        FUNCTION_URL,
        json={"nx": NX, "ny": NY},
        timeout=20,
    )
    r.raise_for_status()
    data = r.json()
    # TODO: data["summary"]를 DB/캐시에 저장하고, 비 오는 날짜 처리 로직 수행
    print(datetime.now(), data["summary"])

if __name__ == "__main__":
    while True:
        try:
            fetch_and_store()
        except Exception as e:
            print("ERROR:", e)
        time.sleep(3600)  # 1시간