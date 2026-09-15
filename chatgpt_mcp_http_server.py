"""
chatgpt_mcp_http_server.py

A remote (HTTP-based) MCP server exposing ChatGPT as a tool, so Claude can
consult ChatGPT and combine perspectives. Deployed publicly over HTTPS so it
works as a custom connector in Claude's web/mobile/desktop apps.

SETUP
-----
1. Install dependencies:
     pip install -r requirements.txt

2. Set environment variables:
     OPENAI_API_KEY   - required. Your OpenAI API key.
     MCP_AUTH_TOKEN   - strongly recommended. A secret string. If set, every
                         request must include header:
                           Authorization: Bearer <MCP_AUTH_TOKEN>
                         Without this, ANYONE who finds your public URL can
                         call ask_chatgpt and spend your OpenAI credits.
     PORT             - optional, defaults to 8000.
     OPENAI_DEFAULT_MODEL - optional, defaults to "gpt-4o".

3. Run locally to test:
     python chatgpt_mcp_http_server.py

4. Deploy (see README.md for step-by-step Railway/Render/Fly instructions).

5. In Claude: Settings > Connectors > Add custom connector, point at
     https://your-app-url/mcp
   and, if you set MCP_AUTH_TOKEN, paste it in as the connector's auth token.
"""

import logging
import os
from typing import Optional

from mcp.server.fastmcp import FastMCP
from openai import APIError, APITimeoutError, OpenAI, RateLimitError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("chatgpt-connector")

DEFAULT_MODEL = os.environ.get("OPENAI_DEFAULT_MODEL", "gpt-4o")

# Models this connector will actually call. Keeping an allowlist means a
# typo'd or malicious model name from a client can't cause weird billing
# surprises or errors deep in the OpenAI SDK.
ALLOWED_MODELS = {
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4.1",
    "gpt-4.1-mini",
    "o3",
    "o3-mini",
    "o4-mini",
}

MAX_PROMPT_CHARS = 20_000  # guardrail against runaway token bills

mcp = FastMCP("chatgpt-connector", stateless_http=True)
client = OpenAI()  # reads OPENAI_API_KEY from environment


def _call_openai(
    prompt: str,
    model: str,
    system: Optional[str] = None,
    max_tokens: int = 1000,
) -> str:
    """Shared call path used by every tool. Centralizes validation,
    the allowlist, and error handling so each tool stays simple."""
    if not os.environ.get("OPENAI_API_KEY"):
        return "Error: OPENAI_API_KEY is not set on the server."
    if not prompt or not prompt.strip():
        return "Error: prompt cannot be empty."
    if len(prompt) > MAX_PROMPT_CHARS:
        return f"Error: prompt exceeds {MAX_PROMPT_CHARS} character limit."
    if model not in ALLOWED_MODELS:
        logger.info("Model %s not in allowlist, falling back to %s", model, DEFAULT_MODEL)
        model = DEFAULT_MODEL

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            timeout=60,
        )
        return response.choices[0].message.content or "(ChatGPT returned an empty response)"
    except RateLimitError:
        return "Error: OpenAI rate limit hit. Try again shortly."
    except APITimeoutError:
        return "Error: OpenAI request timed out after 60s."
    except APIError as e:
        logger.exception("OpenAI API error")
        return f"Error calling OpenAI API: {e}"
    except Exception as e:  # noqa: BLE001 - want to surface anything unexpected to the caller
        logger.exception("Unexpected error calling OpenAI")
        return f"Unexpected error: {e}"


@mcp.tool()
def ask_chatgpt(prompt: str, model: str = DEFAULT_MODEL, system_prompt: Optional[str] = None) -> str:
    """
    Send a single-turn prompt to ChatGPT (OpenAI) and return its response.

    Args:
        prompt: The question or instruction to send to ChatGPT.
        model: Which OpenAI model to use. Falls back to the default if the
            requested model isn't in the server's allowlist.
        system_prompt: Optional system message to set ChatGPT's role or
            constraints for this call (e.g. "Be terse and skeptical.").

    Returns:
        The text of ChatGPT's response, or a message starting with "Error:"
        if the call failed.
    """
    return _call_openai(prompt, model, system=system_prompt)


@mcp.tool()
def ask_chatgpt_with_history(messages: list[dict], model: str = DEFAULT_MODEL) -> str:
    """
    Send a multi-turn conversation to ChatGPT and return its next reply.
    Use this for back-and-forth exchanges (e.g. a debate) by passing the
    growing transcript each time: append ChatGPT's previous reply and your
    own new point as additional messages, then call this again.

    Args:
        messages: List of {"role": "user"|"assistant"|"system", "content": str}
            dicts representing the conversation so far, oldest first. Use
            role "user" for turns that come from you (Claude) and role
            "assistant" for ChatGPT's own prior replies.
        model: Which OpenAI model to use.

    Returns:
        ChatGPT's next reply as text, or a message starting with "Error:".
    """
    if not os.environ.get("OPENAI_API_KEY"):
        return "Error: OPENAI_API_KEY is not set on the server."
    if not messages:
        return "Error: messages cannot be empty."
    if model not in ALLOWED_MODELS:
        model = DEFAULT_MODEL

    cleaned = []
    total_chars = 0
    for m in messages:
        role = m.get("role")
        content = m.get("content", "")
        if role not in ("user", "assistant", "system"):
            return f"Error: invalid role '{role}' in messages."
        total_chars += len(content)
        cleaned.append({"role": role, "content": content})
    if total_chars > MAX_PROMPT_CHARS:
        return f"Error: conversation exceeds {MAX_PROMPT_CHARS} character limit."

    try:
        response = client.chat.completions.create(
            model=model,
            messages=cleaned,
            max_tokens=1000,
            timeout=60,
        )
        return response.choices[0].message.content or "(ChatGPT returned an empty response)"
    except RateLimitError:
        return "Error: OpenAI rate limit hit. Try again shortly."
    except APITimeoutError:
        return "Error: OpenAI request timed out after 60s."
    except APIError as e:
        logger.exception("OpenAI API error")
        return f"Error calling OpenAI API: {e}"
    except Exception as e:  # noqa: BLE001
        logger.exception("Unexpected error calling OpenAI")
        return f"Unexpected error: {e}"


@mcp.tool()
def list_chatgpt_models() -> str:
    """List the OpenAI models this connector is allowed to call."""
    return ", ".join(sorted(ALLOWED_MODELS)) + f" (default: {DEFAULT_MODEL})"


# ---------------------------------------------------------------------------
# Auth middleware
#
# This server is meant to be deployed publicly so mobile clients can reach
# it. Without a shared secret, anyone who discovers the URL can call
# ask_chatgpt and burn your OpenAI credits. If MCP_AUTH_TOKEN is set, every
# request must carry a matching bearer token; if it's unset, the server logs
# a warning and runs open (useful only for quick local testing).
# ---------------------------------------------------------------------------
class BearerAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, token: str):
        super().__init__(app)
        self.token = token

    async def dispatch(self, request: Request, call_next):
        auth = request.headers.get("authorization", "")
        expected = f"Bearer {self.token}"
        if auth != expected:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


def build_app():
    app = mcp.streamable_http_app()
    token = os.environ.get("MCP_AUTH_TOKEN")
    if token:
        app.add_middleware(BearerAuthMiddleware, token=token)
    else:
        logger.warning(
            "MCP_AUTH_TOKEN is not set - this server will accept requests from "
            "anyone who has the URL. Set MCP_AUTH_TOKEN before deploying publicly."
        )
    return app


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(build_app(), host="0.0.0.0", port=port)
