import os
import requests
from datetime import datetime
from typing import Any, Dict, Optional, Tuple
from apscheduler.schedulers.background import BackgroundScheduler

_scheduler: Optional[BackgroundScheduler] = None


def _get_cfg() -> Tuple[str, str, int, int, int, bool]:
    """Read env at runtime to avoid import-time empty values."""
    function_url = os.getenv("FUNCTION_URL", "")
    audience = os.getenv("FUNCTION_AUDIENCE", function_url)
    default_nx = int(os.getenv("DEFAULT_NX", "92"))
    default_ny = int(os.getenv("DEFAULT_NY", "131"))
    poll_minutes = int(os.getenv("WEATHER_POLL_MINUTES", "60"))
    is_private = os.getenv("FUNCTION_PRIVATE", "false").lower() in ("1", "true", "yes")
    return function_url, audience, default_nx, default_ny, poll_minutes, is_private


def fetch_weather_summary(nx: int, ny: int) -> Dict[str, Any]:
    function_url, audience, _nx, _ny, _poll, is_private = _get_cfg()
    if not function_url:
        raise RuntimeError("FUNCTION_URL not set")
    headers = {"Content-Type": "application/json"}
    if is_private:
        try:
            from google.oauth2 import id_token
            from google.auth.transport.requests import Request as GoogleRequest
            token = id_token.fetch_id_token(GoogleRequest(), audience)
            headers["Authorization"] = f"Bearer {token}"
        except Exception:
            # 퍼블릭이면 토큰 없이 진행
            pass
    resp = requests.post(
        function_url,
        json={"nx": nx, "ny": ny},
        headers=headers,
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


def _job():
    try:
        _, _, default_nx, default_ny, _, _ = _get_cfg()
        data = fetch_weather_summary(default_nx, default_ny)
        summary = (data or {}).get("summary", {})
        # 날짜 형식 변환: 20250920 -> 2025-09-20
        rainy = []
        for d, v in summary.items():
            if (v or {}).get("rain_condition") == 1:
                try:
                    # 20250920 -> 2025-09-20 변환
                    if len(d) == 8 and d.isdigit():
                        iso_date = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
                        rainy.append(iso_date)
                    else:
                        rainy.append(d)  # 이미 올바른 형식이면 그대로
                except:
                    rainy.append(d)  # 변환 실패시 원본 유지
        rainy = sorted(rainy)
        print(f"[weather] {datetime.now().isoformat()} nx={default_nx} ny={default_ny} rainy={rainy}")
    except Exception as e:
        print(f"[scheduler][ERROR] {e}")


def start_weather_scheduler(app=None):
    global _scheduler
    function_url, audience, _, _, poll_minutes, _ = _get_cfg()
    if not function_url:
        print("[scheduler] skipped: FUNCTION_URL not set")
        return
    if _scheduler:
        return
    _scheduler = BackgroundScheduler()
    _scheduler.add_job(_job, "interval", minutes=poll_minutes, id="weather-poll", coalesce=True, max_instances=1)
    _scheduler.start()
    print(f"[scheduler] started: every {poll_minutes} minutes (FUNCTION_URL={function_url})")


def stop_weather_scheduler():
    global _scheduler
    if _scheduler:
        _scheduler.shutdown()
        _scheduler = None
        print("[scheduler] stopped")
