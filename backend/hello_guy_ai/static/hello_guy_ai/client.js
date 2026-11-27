/* API + SSE client for the chat page. No dependencies. */
(function () {
  "use strict";

  function csrfToken() {
    const row = document.cookie.split("; ").find((c) => c.startsWith("csrftoken="));
    return row ? decodeURIComponent(row.split("=")[1]) : "";
  }

  function createClient(apiBase) {
    const base = apiBase.replace(/\/$/, "");

    async function request(path, options = {}) {
      const res = await fetch(base + path, {
        credentials: "same-origin",
        ...options,
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken(), ...(options.headers || {}) },
      });
      if (res.status === 204) return null;
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const err = new Error(data.message || data.detail?.[0]?.msg || `Request failed (${res.status})`);
        err.status = res.status;
        throw err;
      }
      return data;
    }

    return {
      assistants: () => request("/assistants"),
      threads: (assistantId) => request(`/threads?limit=50&assistant_id=${encodeURIComponent(assistantId)}`),
      createThread: (assistantId) =>
        request("/threads", { method: "POST", body: JSON.stringify({ assistant_id: assistantId }) }),
      messages: (threadId) => request(`/threads/${threadId}/messages?limit=100`),
      // The browser's timezone lets the assistant read "5 baje" as 5 PM *here* (reminders, date ranges).
      send: (threadId, content) =>
        request(`/threads/${threadId}/messages`, {
          method: "POST",
          body: JSON.stringify({ content, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "" }),
        }),
      notifications: () => request("/notifications?limit=30"),
      unreadCount: () => request("/notifications/unread-count"),
      markRead: (ids) => request("/notifications/read", { method: "POST", body: JSON.stringify({ ids }) }),
      reminders: () => request("/reminders?limit=100"),
      pauseReminder: (id) => request(`/reminders/${id}/pause`, { method: "POST" }),
      resumeReminder: (id) => request(`/reminders/${id}/resume`, { method: "POST" }),
      deleteReminder: (id) => request(`/reminders/${id}`, { method: "DELETE" }),
      thread: (threadId) => request(`/threads/${threadId}`),
      resume: (runId, answers) =>
        request(`/runs/${runId}/resume`, { method: "POST", body: JSON.stringify({ answers }) }),
      stop: (runId) => request(`/runs/${runId}/stop`, { method: "POST" }),
      feedback: (runId, rating, comment = "") =>
        request(`/runs/${runId}/feedback`, { method: "POST", body: JSON.stringify({ rating, comment }) }),
    };
  }

  /**
   * Follow a run's SSE stream. Returns a close() function.
   * handlers: onToken(segmentText), onToolStart(data), onToolEnd(data), onSources(list),
   *           onDone(finalText|undefined, data), onError(message), onPaused(questions), onStopped()
   * A text segment restarts after each tool call, so text before and after tools stays separate.
   */
  function streamRun(url, handlers) {
    const source = new EventSource(url, { withCredentials: true });
    let text = "";
    let finished = false;
    const finish = () => { finished = true; source.close(); };
    const parse = (e) => { try { return JSON.parse(e.data); } catch { return {}; } };

    source.addEventListener("token", (e) => { text += parse(e).text || ""; handlers.onToken?.(text); });
    source.addEventListener("tool_start", (e) => { text = ""; handlers.onToolStart?.(parse(e)); });
    source.addEventListener("tool_end", (e) => { text = ""; handlers.onToolEnd?.(parse(e)); });
    source.addEventListener("sources", (e) => handlers.onSources?.(parse(e).sources || []));
    source.addEventListener("done", (e) => { finish(); const d = parse(e); handlers.onDone?.(d.content, d); });
    source.addEventListener("paused", (e) => { finish(); handlers.onPaused?.(parse(e).questions || []); });
    source.addEventListener("stopped", () => { finish(); handlers.onStopped?.(); });
    source.addEventListener("error", (e) => {
      if (finished) return;
      if (e.data) { finish(); handlers.onError?.(parse(e).message || "Assistant failed"); return; }
      // CLOSED = server refused (HTTP error): the browser won't retry, so tell the user.
      if (source.readyState === EventSource.CLOSED) { finish(); handlers.onError?.("Connection to the assistant failed. Please try again."); }
      // Otherwise a network blip: EventSource reconnects itself with Last-Event-ID.
    });
    return finish;
  }

  window.HelloGuy = { ...(window.HelloGuy || {}), createClient, streamRun };
})();
