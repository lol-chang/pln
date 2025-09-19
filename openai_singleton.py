# openai_singleton.py
import os
import re
import json
import logging
from functools import lru_cache
from threading import RLock
from typing import Optional, Any, Dict, Union, Tuple

from openai import OpenAI

# ── 환경 변수 ─────────────────────────────────────────────────────────
DEFAULT_OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
DEFAULT_OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
DEFAULT_DEBUG = os.getenv("OPENAI_DEBUG", "0") in ("1", "true", "True")

# ── 로깅 ───────────────────────────────────────────────────────────────
logger = logging.getLogger("openai_singleton")
if not logger.handlers:
    handler = logging.StreamHandler()
    fmt = logging.Formatter("[%(levelname)s] %(name)s: %(message)s")
    handler.setFormatter(fmt)
    logger.addHandler(handler)
logger.setLevel(logging.INFO if not DEFAULT_DEBUG else logging.DEBUG)

_LOCK = RLock()

# ── 싱글톤 팩토리 ────────────────────────────────────────────────────
@lru_cache(maxsize=None)
def get_openai_client(
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> OpenAI:
    """
    (api_key, base_url) 조합별 단일 OpenAI 클라이언트.
    """
    with _LOCK:
        key = api_key or DEFAULT_OPENAI_API_KEY
        if not key:
            raise ValueError("OPENAI_API_KEY가 설정되지 않았습니다. 환경변수나 인자로 전달하세요.")
        client = OpenAI(
            api_key=key,
            base_url=base_url or DEFAULT_OPENAI_BASE_URL,
        )
        logger.debug("OpenAI client created (singleton). base_url=%s", base_url or DEFAULT_OPENAI_BASE_URL)
        return client


def reset_openai_singleton() -> None:
    """캐시 초기화 → 다음 호출에서 새 인스턴스 생성"""
    with _LOCK:
        get_openai_client.cache_clear()
        logger.debug("OpenAI singleton cache cleared.")


# ── 내부 헬퍼: 안전 파서 ────────────────────────────────────────────
_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)

def _strip_code_fences(text: str) -> str:
    """
    ```json ... ``` / ``` ... ``` 로 감싸진 경우 내부만 추출.
    """
    m = _CODE_FENCE_RE.search(text or "")
    return m.group(1).strip() if m else (text or "").strip()

def _extract_json_snippet(text: str) -> str:
    """
    앞뒤 설명 섞여도 JSON 오브젝트/배열만 추출.
    """
    s = _strip_code_fences(text)
    start_obj = s.find("{")
    start_arr = s.find("[")
    if start_obj == -1 and start_arr == -1:
        return s
    start = min(x for x in (start_obj, start_arr) if x != -1)
    end_obj = s.rfind("}")
    end_arr = s.rfind("]")
    end = max(end_obj, end_arr)
    if end != -1 and end >= start:
        return s[start:end + 1].strip()
    return s[start:].strip()


# ── 편의 헬퍼: 텍스트 ────────────────────────────────────────────────
def llm_text(
    prompt: str,
    *,
    model: str = "gpt-4o",
    client: Optional[OpenAI] = None,
    debug: Optional[bool] = None,
    **kwargs: Any,
) -> str:
    """
    간단 텍스트 출력용. responses.create 호출 후 문자열 반환.
    """
    dbg = DEFAULT_DEBUG if debug is None else debug
    cli = client or get_openai_client()
    resp = cli.responses.create(model=model, input=prompt, **kwargs)
    text = getattr(resp, "output_text", None) or str(resp)
    if dbg:
        logger.debug("llm_text raw:\n%s", text)
    return text


# ── 편의 헬퍼: JSON (관대 파싱) ──────────────────────────────────────
def llm_json(
    prompt: str,
    *,
    model: str = "gpt-4o",
    client: Optional[OpenAI] = None,
    debug: Optional[bool] = None,
    return_tuple: bool = False,
    **kwargs: Any,
) -> Union[Dict[str, Any], Tuple[Optional[Union[dict, list]], str, str]]:
    """
    JSON 응답 기대 시 사용.
    - 코드블록/앞뒤 텍스트 섞여도 안전 파싱 시도
    - debug=True 이면 raw/snippet 로그를 남김
    - return_tuple=True 이면 (parsed, raw, snippet) 튜플 반환
    """
    dbg = DEFAULT_DEBUG if debug is None else debug
    cli = client or get_openai_client()
    resp = cli.responses.create(model=model, input=prompt, **kwargs)
    raw = getattr(resp, "output_text", None) or str(resp)
    snippet = _extract_json_snippet(raw)

    if dbg:
        logger.debug("llm_json raw:\n%s", raw)
        logger.debug("llm_json snippet:\n%s", snippet)

    try:
        parsed = json.loads(snippet)
        if return_tuple:
            return parsed, raw, snippet
        return parsed  # type: ignore[return-value]
    except json.JSONDecodeError as e:
        err = {"error": f"OpenAI 응답 JSON 파싱 실패: {e}", "raw": raw, "snippet": snippet}
        if return_tuple:
            return None, raw, snippet
        return err  # type: ignore[return-value]


# ── 편의 헬퍼: JSON (엄격 모드 / 스키마 강제) ───────────────────────
def llm_json_strict(
    prompt: str,
    *,
    model: str = "gpt-4o",
    client: Optional[OpenAI] = None,
    json_schema: Optional[Dict[str, Any]] = None,
    debug: Optional[bool] = None,
    **kwargs: Any,
) -> Union[dict, list]:
    """
    버전 호환 엄격 JSON:
      1) Responses API + json_schema (신 SDK)
      2) 실패 시 Chat Completions + json_object
      3) 마지막 폴백: 관대 파싱(_extract_json_snippet)
    """
    dbg = DEFAULT_DEBUG if debug is None else debug
    cli = client or get_openai_client()

    # 1) Responses API + json_schema 시도
    schema = json_schema or {
        "name": "ListOfStrings",
        "schema": {"type": "array", "items": {"type": "string"}},
        "strict": True,
    }
    try:
        resp = cli.responses.create(
            model=model,
            input=prompt,
            response_format={"type": "json_schema", "json_schema": schema},
            temperature=0,
            **kwargs,
        )
        text = getattr(resp, "output_text", None) or str(resp)
        if dbg: logger.debug("llm_json_strict (responses) raw:\n%s", text)
        return json.loads(text)

    except TypeError as te:
        # responses.create 가 response_format을 모르는 구버전
        if dbg: logger.debug("responses.create response_format 미지원: %s", te)

    except Exception as e:
        # 다른 오류는 다음 단계로 폴백
        if dbg: logger.debug("responses.create 실패, chat로 폴백: %s", e)

    # 2) Chat Completions + json_object 시도
    try:
        messages = [
            {"role": "system", "content": "Return ONLY valid JSON. No markdown or extra text."},
            {"role": "user", "content": prompt},
        ]
        chat = cli.chat.completions.create(
            model=model,
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0,
            **kwargs,
        )
        text = chat.choices[0].message.content
        if dbg: logger.debug("llm_json_strict (chat) raw:\n%s", text)
        return json.loads(text)

    except TypeError as te:
        # chat.completions 가 response_format을 모르는 더 구버전
        if dbg: logger.debug("chat.completions response_format 미지원: %s", te)

    except Exception as e:
        if dbg: logger.debug("chat.completions 실패, 관대 파싱으로 폴백: %s", e)

    # 3) 최종 폴백: 관대 파싱
    try:
        # 기존 llm_json 로직 재사용
        parsed_or_err = llm_json(prompt, model=model, client=cli, debug=dbg, **kwargs)
        if isinstance(parsed_or_err, dict) and "error" in parsed_or_err:
            raise ValueError(parsed_or_err["error"])
        return parsed_or_err  # dict or list

    except Exception as e:
        # 여기도 실패하면 명시적으로 에러를 올려줌
        raise RuntimeError(f"모든 JSON 강제 시도가 실패했습니다: {e}")

