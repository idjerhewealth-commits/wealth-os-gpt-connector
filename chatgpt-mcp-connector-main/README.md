# ChatGPT MCP Connector

Lets Claude call `ask_chatgpt` (and friends) as a tool, so Claude can consult
ChatGPT mid-conversation.

## Files
- `chatgpt_mcp_http_server.py` — the MCP server (FastMCP, streamable HTTP)
- `requirements.txt` — Python deps
- `Dockerfile` — for Fly.io / any container host
- `Procfile` — for Render/Railway if you don't use the Dockerfile
- `.env.example` — env vars you need to set

## Tools exposed
- `ask_chatgpt(prompt, model, system_prompt)` — single-turn question
- `ask_chatgpt_with_history(messages, model)` — multi-turn, for debate-style exchanges. Pass a growing transcript as `[{"role": "user"/"assistant"/"system", "content": "..."}]`
- `list_chatgpt_models()` — see what's allowed

For a "debate," Claude drives it by calling `ask_chatgpt_with_history` repeatedly: Claude states its position as a `user` message, sends the transcript, gets ChatGPT's reply back as `assistant`, appends its own rebuttal as another `user` message, and calls again. No special server-side "debate mode" needed — this keeps Claude in control of the conversation instead of two models looping unsupervised.

## 1. Deploy

### Option A: Railway (simplest)
1. Push these files to a GitHub repo (or use `railway up` from this folder with the Railway CLI).
2. In Railway: New Project → Deploy from repo (or CLI).
3. Project → Variables: set `OPENAI_API_KEY` and `MCP_AUTH_TOKEN`.
4. Railway auto-detects Python and uses the `Procfile`. Deploy.
5. Copy the generated public URL (Settings → Networking → Generate Domain).

### Option B: Render
1. New → Web Service → connect your repo.
2. Runtime: Python 3. Build command: `pip install -r requirements.txt`. Start command: `python chatgpt_mcp_http_server.py`.
3. Environment → add `OPENAI_API_KEY` and `MCP_AUTH_TOKEN`.
4. Deploy, copy the `https://your-app.onrender.com` URL.

### Option C: Fly.io (uses the Dockerfile)
```bash
fly launch --no-deploy      # creates fly.toml, don't let it overwrite the Dockerfile
fly secrets set OPENAI_API_KEY=sk-... MCP_AUTH_TOKEN=your-secret
fly deploy
```

Whichever you pick, generate a strong `MCP_AUTH_TOKEN` first:
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```
Without it, the server is public and open — anyone with the URL can call `ask_chatgpt` on your OpenAI bill.

## 2. Test it's alive
```bash
curl -i https://your-app-url/mcp \
  -H "Authorization: Bearer your-secret" \
  -H "Content-Type: application/json"
```
A 401 means the auth header is missing/wrong. A 4xx/5xx that isn't 401 usually means the server started but the request shape wasn't a valid MCP request — that's expected from a bare curl; the real client is Claude's connector, not curl.

## 3. Connect in Claude
Settings → Connectors → Add custom connector:
- URL: `https://your-app-url/mcp`
- Auth: paste your `MCP_AUTH_TOKEN` as the bearer token if the connector UI has an auth field; otherwise check Anthropic's current docs for how custom connectors pass auth headers (this can change — search `docs.claude.com` if the field isn't where you expect).

## Notes / limits
- `MAX_PROMPT_CHARS` (20,000) and `max_tokens` (1000 per call) are set conservatively to avoid surprise bills — raise them in the code if you need longer exchanges.
- `ALLOWED_MODELS` is an allowlist; add/remove model names there as OpenAI's lineup changes.
- Logging goes to stdout — check your host's log viewer if calls seem to silently fail.
