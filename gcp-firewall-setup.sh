#!/bin/bash

echo "🔥 GCP FastAPI 방화벽 설정 스크립트"
echo "=================================="

# 변수 설정 (실제 값으로 변경 필요)
INSTANCE_NAME="your-instance-name"
ZONE="your-zone"
PROJECT_ID="your-project-id"

echo "📋 현재 설정:"
echo "- Instance: $INSTANCE_NAME"
echo "- Zone: $ZONE" 
echo "- Project: $PROJECT_ID"
echo ""

# 1. 방화벽 규칙 생성
echo "🛡️  방화벽 규칙 생성 중..."
gcloud compute firewall-rules create allow-fastapi-8000 \
    --allow tcp:8000 \
    --source-ranges 0.0.0.0/0 \
    --target-tags fastapi-server \
    --description "Allow FastAPI on port 8000" \
    --project=$PROJECT_ID

# 2. VM에 태그 추가
echo "🏷️  VM에 태그 추가 중..."
gcloud compute instances add-tags $INSTANCE_NAME \
    --tags fastapi-server \
    --zone $ZONE \
    --project=$PROJECT_ID

# 3. 외부 IP 확인
echo "🌐 외부 IP 확인 중..."
EXTERNAL_IP=$(gcloud compute instances describe $INSTANCE_NAME \
    --zone=$ZONE \
    --project=$PROJECT_ID \
    --format='get(networkInterfaces[0].accessConfigs[0].natIP)')

echo ""
echo "✅ 설정 완료!"
echo "🚀 서버 실행: uvicorn main:app --host 0.0.0.0 --port 8000"
echo "🔗 접속 URL: http://$EXTERNAL_IP:8000"
echo "📚 API 문서: http://$EXTERNAL_IP:8000/docs"
echo ""
echo "⚠️  VM 내부에서도 방화벽 확인:"
echo "   sudo ufw allow 8000"
echo "   # 또는"  
echo "   sudo firewall-cmd --add-port=8000/tcp --permanent"
