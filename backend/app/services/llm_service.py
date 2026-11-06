import logging
import asyncio
from typing import AsyncGenerator, Dict, List, Optional
import httpx
import openai

logger = logging.getLogger(__name__)

# Module-level shared httpx.AsyncClient with connection pooling and keepalive
_SHARED_HTTP_CLIENT: Optional[httpx.AsyncClient] = None
_CLIENT_CACHE: Dict[str, openai.AsyncOpenAI] = {}
_KEEP_WARM_TASK: Optional[asyncio.Task] = None


def get_shared_http_client() -> httpx.AsyncClient:
    global _SHARED_HTTP_CLIENT
    if _SHARED_HTTP_CLIENT is None or _SHARED_HTTP_CLIENT.is_closed:
        _SHARED_HTTP_CLIENT = httpx.AsyncClient(
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20, keepalive_expiry=300),
            timeout=httpx.Timeout(20.0, connect=5.0)
        )
    return _SHARED_HTTP_CLIENT


def get_openai_client(api_key: str) -> Optional[openai.AsyncOpenAI]:
    """Builds or returns a cached AsyncOpenAI client reusing the shared pooled HTTP client."""
    if not api_key:
        return None
    if api_key not in _CLIENT_CACHE:
        client = openai.AsyncOpenAI(
            api_key=api_key,
            http_client=get_shared_http_client(),
            max_retries=1
        )
        _CLIENT_CACHE[api_key] = client
    return _CLIENT_CACHE[api_key]


async def keep_warm_openai():
    """Background task to keep OpenAI TCP/TLS connection open across turns."""
    from app.core.config import settings
    api_key = settings.openai_api_key
    if not api_key:
        return
    client = get_openai_client(api_key)
    if not client:
        return

    while True:
        try:
            # Cheap request to keep connection pool warm
            await client.models.retrieve("gpt-4o-mini")
            logger.debug("[LLM KeepWarm] Successfully pinged OpenAI models endpoint")
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.debug(f"[LLM KeepWarm] Ping suppressed: {e}")
        await asyncio.sleep(30)


def start_llm_keep_warm() -> Optional[asyncio.Task]:
    global _KEEP_WARM_TASK
    if _KEEP_WARM_TASK is None or _KEEP_WARM_TASK.done():
        _KEEP_WARM_TASK = asyncio.create_task(keep_warm_openai())
    return _KEEP_WARM_TASK


def stop_llm_keep_warm():
    global _KEEP_WARM_TASK
    if _KEEP_WARM_TASK and not _KEEP_WARM_TASK.done():
        _KEEP_WARM_TASK.cancel()
        _KEEP_WARM_TASK = None


class LLMService:
    def __init__(self, openai_key: Optional[str], anthropic_key: Optional[str] = None):
        self.openai_key = openai_key
        self.openai_client = get_openai_client(openai_key) if openai_key else None

    # ──────────────────────────────────────────
    # Non-streaming (used by phone webhook)
    # ──────────────────────────────────────────
    async def generate(
        self,
        system_prompt: str,
        messages: List[Dict[str, str]],
        model: str = "gpt-4o-mini",
        temperature: float = 0.7,
        max_tokens: int = 150,
    ) -> str:
        import time
        start_time = time.time()
        provider = "openai"
        try:
            # Force GPT-4o mini
            model_to_use = "gpt-4o-mini"
            if not self.openai_client:
                raise ValueError("OpenAI API key not configured")
            
            response = await self.openai_client.chat.completions.create(
                model=model_to_use,
                messages=[{"role": "system", "content": system_prompt}] + messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            res_content = response.choices[0].message.content or ""
            latency = int((time.time() - start_time) * 1000)
            from app.services.provider_health_service import log_provider_health_event
            asyncio.create_task(asyncio.to_thread(
                log_provider_health_event, provider, "llm", "success", latency_ms=latency
            ))
            return res_content
        except Exception as e:
            latency = int((time.time() - start_time) * 1000)
            from app.services.provider_health_service import log_provider_health_event
            status = "429_rate_limited" if "rate_limit" in str(e).lower() or "429" in str(e) else "failure"
            asyncio.create_task(asyncio.to_thread(
                log_provider_health_event, provider, "llm", status, latency_ms=latency, error_code="API_ERROR", error_message=str(e)
            ))
            logger.error("LLM generate failed: %s", e)
            raise

    # ──────────────────────────────────────────
    # Streaming — yields raw token strings
    # ──────────────────────────────────────────
    async def generate_stream(
        self,
        system_prompt: str,
        messages: List[Dict[str, str]],
        model: str = "gpt-4o-mini",
        temperature: float = 0.7,
        max_tokens: int = 80,
    ) -> AsyncGenerator[str, None]:
        if not self.openai_client:
            raise ValueError("OpenAI API key not configured")

        # Force GPT-4o mini
        model_to_use = "gpt-4o-mini"

        import time
        start_time = time.time()
        first_token_logged = False
        try:
            stream = await self.openai_client.chat.completions.create(
                model=model_to_use,
                messages=[{"role": "system", "content": system_prompt}] + messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    if not first_token_logged:
                        latency = int((time.time() - start_time) * 1000)
                        from app.services.provider_health_service import log_provider_health_event
                        asyncio.create_task(asyncio.to_thread(
                            log_provider_health_event, "openai", "llm", "success", latency_ms=latency
                        ))
                        first_token_logged = True
                    yield delta
        except Exception as e:
            latency = int((time.time() - start_time) * 1000)
            from app.services.provider_health_service import log_provider_health_event
            status = "429_rate_limited" if "rate_limit" in str(e).lower() or "429" in str(e) else "failure"
            asyncio.create_task(asyncio.to_thread(
                log_provider_health_event, "openai", "llm", status, latency_ms=latency, error_code="STREAM_ERROR", error_message=str(e)
            ))
            logger.error("LLM stream failed: %s", e)
            raise
