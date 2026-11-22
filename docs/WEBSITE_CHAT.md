# Website chat: frontend integration

The public chat lets visitors on your website talk to the **Website Assistant** agent
(`backend/cms/ai_assistants.py`). Visitors do not log in. Every request runs as the
`website-bot` user, which can only:

- view published blog posts, active projects, FAQs, services, testimonials, skills and videos
- create inquiries (leads)

It cannot create or change blogs, read inquiries, or reach any internal chat.

## Endpoints

Base URL: `https://<your-backend>/api/ai/public`

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/agent` | | `{name, description, suggestions[]}` for the welcome screen |
| POST | `/threads` | | `201 {thread_id}` (rate limit: 10/min per IP) |
| GET | `/threads/{thread_id}/messages` | | `{items: [{role, content, run_status, run_error, …}]}` history |
| POST | `/threads/{thread_id}/messages` | `{"content": "…"}` (max 2000 chars) | `202 {run_id, stream_url}` (rate limit: 10/min per IP) |
| GET | `stream_url` from the POST (`/api/ai/runs/{run_id}/stream`) | | Server-Sent Events |

The `thread_id` is the visitor's key to their conversation. Store it in `localStorage`
so the chat survives page reloads.

### Stream events

| Event | Data | What to do |
|---|---|---|
| `token` | `{text}` | Append `text` to the current answer |
| `tool_start` | `{id, name, args}` | Optional: show "Searching projects…" |
| `tool_end` | `{id, name, error}` | Optional: mark the step done. The answer restarts after a tool |
| `done` | `{content}` | Final answer text (replace what you streamed). Close the stream |
| `error` | `{message}` | Show `message` to the visitor. Close the stream |

Answers are Markdown. Render them with your Markdown library (for example `react-markdown`).

## Minimal example (plain JavaScript)

```js
const API = "https://<your-backend>/api/ai/public";

async function getThread() {
  let id = localStorage.getItem("hg-thread");
  if (!id) {
    const res = await fetch(`${API}/threads`, { method: "POST" });
    id = (await res.json()).thread_id;
    localStorage.setItem("hg-thread", id);
  }
  return id;
}

async function ask(question, onText) {
  const thread = await getThread();
  const res = await fetch(`${API}/threads/${thread}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content: question }),
  });
  if (res.status === 404) { localStorage.removeItem("hg-thread"); return ask(question, onText); } // thread gone
  if (!res.ok) throw new Error((await res.json()).message || "Request failed"); // 429 = too many messages
  const { stream_url } = await res.json();

  return new Promise((resolve, reject) => {
    const source = new EventSource(new URL(stream_url, API).href); // stream_url is a path on the backend
    let text = "";
    source.addEventListener("token", (e) => { text += JSON.parse(e.data).text; onText(text); });
    source.addEventListener("tool_end", () => { text = ""; });
    source.addEventListener("done", (e) => { source.close(); const d = JSON.parse(e.data); onText(d.content ?? text); resolve(); });
    source.addEventListener("error", (e) => {
      source.close();
      reject(new Error(e.data ? JSON.parse(e.data).message : "Connection lost. Please try again."));
    });
  });
}

// Usage
ask("What services do you offer?", (markdown) => { answerEl.textContent = markdown; });
```

## Production checklist

1. **CORS:** in the backend `.env`, set `CORS_ALLOW_ALL_ORIGINS=False` and
   `CORS_ALLOWED_ORIGINS=https://your-website.com`.
2. **Worker:** the worker process must run (`./run.sh` locally, Supervisor or systemd in production).
3. **Streaming behind nginx:** the stream sends `X-Accel-Buffering: no`. If you use another proxy,
   turn off response buffering for `/api/ai/public/runs/`.
4. **Real visitor IPs:** rate limits use the client IP. Behind a proxy, set `NINJA_NUM_PROXIES`
   in Django settings, or every visitor shares one limit.
5. **LLM limits:** every visitor message uses your LLM API quota. Watch the provider's rate limits.

## Change the agent

Edit `WebsiteAssistant` in `backend/cms/ai_assistants.py` (instructions, tools, suggestions).
To switch the public chat off, set `HELLO_GUY_AI["PUBLIC_AGENT"] = ""` in settings.
