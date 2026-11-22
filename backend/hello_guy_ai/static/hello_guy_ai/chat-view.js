/* Chat rendering: streamed Markdown answers, live tool steps, approval cards, stop, copy, feedback.
 * Model and user text only ever reaches the DOM as text nodes (renderMarkdown builds nodes, no innerHTML).
 */
(function () {
  "use strict";

  const TOOL_LABELS = {
    site_content_summary: "Counting website content",
    search_blog_posts: "Searching blog posts",
    get_blog_post: "Reading blog post",
    search_projects: "Searching projects",
    search_faqs: "Searching FAQs",
    list_inquiries: "Loading inquiries",
    get_inquiry: "Reading inquiry",
    mark_inquiry_replied: "Marking inquiry as replied",
    top_blog_posts: "Ranking blog posts",
    create_blog_draft: "Saving blog draft",
    search_knowledge: "Searching knowledge base",
    update_memory: "Saving to memory",
    get_current_datetime: "Checking date and time",
    get_my_profile: "Loading your profile",
    db_list_models: "Listing data models",
    db_describe: "Inspecting data model",
    db_read: "Reading records",
    db_create: "Creating record",
    db_update: "Updating record",
    db_delete: "Deleting record",
  };

  function toolLabel(name) {
    const text = TOOL_LABELS[name] || String(name || "tool").replace(/_/g, " ");
    return text.charAt(0).toUpperCase() + text.slice(1);
  }

  // Capability name for lists ("Search blog posts"), as opposed to the in-progress step label.
  function toolTitle(name) {
    const text = String(name || "tool").replace(/^db_/, "database ").replace(/_/g, " ");
    return text.charAt(0).toUpperCase() + text.slice(1);
  }

  function argsSummary(args) {
    if (typeof args === "string") { try { args = JSON.parse(args); } catch { return ""; } }
    if (!args || typeof args !== "object") return "";
    const parts = Object.entries(args)
      .filter(([, v]) => v !== "" && v !== null && v !== false && v !== undefined)
      .map(([k, v]) => `${k.replace(/_/g, " ")}: ${typeof v === "object" ? JSON.stringify(v) : v}`);
    const text = parts.join(" · ");
    return text.length > 90 ? text.slice(0, 89) + "…" : text;
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function icon(path, size = 16) {
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("width", size);
    svg.setAttribute("height", size);
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "2");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    svg.setAttribute("aria-hidden", "true");
    const p = document.createElementNS(ns, "path");
    p.setAttribute("d", path);
    svg.appendChild(p);
    return svg;
  }
  const ICONS = {
    copy: "M9 9h11v11H9zM5 15H4V4h11v1",
    check: "M5 12l5 5L20 7",
    up: "M7 10v11M7 10l4-7a2 2 0 0 1 3 2l-1 5h6a2 2 0 0 1 2 2l-2 7a2 2 0 0 1-2 2H7",
    down: "M17 14V3M17 14l-4 7a2 2 0 0 1-3-2l1-5H5a2 2 0 0 1-2-2l2-7a2 2 0 0 1 2-2h10",
  };

  function createChatView({ client, list, onBusy = () => {} }) {
    let current = null; // { runId, close, cancel? }
    let busy = false;

    function setBusy(value) {
      busy = value;
      onBusy(value);
    }

    // Follow the stream only while the reader is at the bottom; never yank them up from history.
    function nearBottom() {
      return list.scrollHeight - list.scrollTop - list.clientHeight < 120;
    }
    function keepBottom(fn) {
      const stick = nearBottom();
      fn();
      if (stick) list.scrollTop = list.scrollHeight;
    }

    function clearEmpty() {
      list.querySelector(".hg-empty")?.remove();
    }

    function addUser(text) {
      clearEmpty();
      const row = el("div", "hg-row hg-row-user");
      row.appendChild(el("div", "hg-bubble", text));
      keepBottom(() => list.appendChild(row));
      list.scrollTop = list.scrollHeight; // your own message always scrolls into view
      return row;
    }

    // ---- assistant turn: a timeline of text segments and tool steps -------------------

    // existingRow: continue a turn already on screen (paused run after reload).
    function createTurn(runId, existingRow = null) {
      clearEmpty();
      let row = existingRow;
      let body = existingRow?.querySelector(".hg-turn");
      if (!row) {
        row = el("div", "hg-row hg-row-ai");
        row.dataset.runId = runId || "";
        row.appendChild(el("div", "hg-avatar", "AI")).setAttribute("aria-hidden", "true");
        body = row.appendChild(el("div", "hg-turn"));
        keepBottom(() => list.appendChild(row));
      }
      row.querySelector(".hg-actions")?.remove();

      let segment = null; // { node, text }
      let thinking = null;
      let frame = 0;

      const turn = {
        row,
        body,
        segments: [],
        thinking(label = "Thinking") {
          keepBottom(() => {
            if (!thinking) {
              thinking = el("div", "hg-thinking");
              thinking.append(el("span", "hg-dots"), el("span", "hg-thinking-label"));
            }
            thinking.lastChild.textContent = label;
            body.appendChild(thinking); // (re)move to the end of the timeline
          });
        },
        stopThinking() {
          thinking?.remove();
        },
        text(value, streaming = true) {
          turn.stopThinking();
          if (!segment) {
            segment = { node: el("div", "hg-md"), text: "" };
            turn.segments.push(segment);
            keepBottom(() => body.appendChild(segment.node));
          }
          segment.text = value;
          segment.node.classList.toggle("is-streaming", streaming);
          const target = segment;
          const paint = () => keepBottom(() => target.node.replaceChildren(window.HelloGuy.renderMarkdown(target.text)));
          cancelAnimationFrame(frame);
          if (streaming) frame = requestAnimationFrame(paint); // at most one re-render per frame
          else paint();
        },
        endSegment() {
          if (segment) segment.node.classList.remove("is-streaming");
          segment = null;
        },
        step(id, name, args, state = "running") {
          turn.endSegment();
          turn.stopThinking();
          let node = id ? body.querySelector(`.hg-step[data-id="${CSS.escape(id)}"]`) : null;
          if (!node) {
            node = el("div", "hg-step");
            if (id) node.dataset.id = id;
            node.append(el("span", "hg-step-icon"), el("span", "hg-step-label", toolLabel(name)));
            const detail = argsSummary(args);
            if (detail) node.appendChild(el("span", "hg-step-args", detail));
            keepBottom(() => body.appendChild(node));
          }
          node.dataset.state = state;
          node.title = { running: "Running…", done: "Done", error: "Failed", pending: "Waiting for your approval", denied: "Denied", redirected: "Changed by you" }[state] || "";
          return node;
        },
        note(text, kind = "") {
          turn.endSegment();
          turn.stopThinking();
          keepBottom(() => body.appendChild(el("div", `hg-note ${kind}`, text)));
        },
        sources(items) {
          if (!items.length) return;
          const box = el("div", "hg-sources");
          box.appendChild(el("span", "hg-sources-label", "Sources"));
          items.forEach((s) => box.appendChild(el("span", "hg-chip", s.title)));
          turn.sourcesBox?.remove();
          turn.sourcesBox = box;
        },
        finish(runId, withFeedback) {
          turn.endSegment();
          turn.stopThinking();
          if (turn.sourcesBox) body.appendChild(turn.sourcesBox);
          if (!turn.segments.length && !body.querySelector(".hg-step, .hg-note, .hg-card")) row.remove();
          else body.appendChild(actions(turn, runId, withFeedback));
        },
      };
      return turn;
    }

    function actions(turn, runId, withFeedback) {
      const bar = el("div", "hg-actions");
      const copy = bar.appendChild(el("button", "hg-act"));
      copy.type = "button";
      copy.title = "Copy answer";
      copy.setAttribute("aria-label", "Copy answer");
      copy.appendChild(icon(ICONS.copy, 15));
      copy.onclick = () => {
        const text = turn.segments.map((s) => s.text).join("\n\n");
        navigator.clipboard?.writeText(text).then(() => {
          copy.replaceChildren(icon(ICONS.check, 15));
          setTimeout(() => copy.replaceChildren(icon(ICONS.copy, 15)), 1500);
        });
      };
      if (withFeedback && runId) {
        const send = async (rating, comment = "") => {
          try {
            await client.feedback(runId, rating, comment);
            fb.replaceChildren(el("span", "hg-thanks", "Thanks for the feedback"));
          } catch (err) {
            fb.replaceChildren(el("span", "hg-thanks", err.message));
          }
        };
        const fb = bar.appendChild(el("span", "hg-feedback"));
        const up = fb.appendChild(el("button", "hg-act"));
        up.type = "button";
        up.title = "Good answer";
        up.setAttribute("aria-label", "Good answer");
        up.appendChild(icon(ICONS.up, 15));
        up.onclick = () => send("up");
        const down = fb.appendChild(el("button", "hg-act"));
        down.type = "button";
        down.title = "Bad answer";
        down.setAttribute("aria-label", "Bad answer");
        down.appendChild(icon(ICONS.down, 15));
        down.onclick = () => {
          const input = el("input", "hg-input-sm");
          input.placeholder = "What went wrong? Press Enter to send";
          input.maxLength = 500;
          input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); send("down", input.value.trim()); } });
          fb.replaceChildren(input);
          input.focus();
        };
      }
      return bar;
    }

    function notice(content) {
      const box = el("div", "hg-empty");
      if (typeof content === "string") box.appendChild(el("p", "", content));
      else box.appendChild(content);
      list.replaceChildren(box);
    }

    // ---- one run: stream -> (pause -> approve -> stream)* -> done ---------------------

    function follow(runId, streamUrl, turn = createTurn(runId)) {
      return new Promise((resolve) => {
        turn.thinking();
        const end = (withFeedback) => { current = null; turn.finish(runId, withFeedback); resolve(); };
        const close = window.HelloGuy.streamRun(streamUrl, {
          onToken: (text) => turn.text(text),
          onToolStart: (d) => {
            turn.step(d.id, d.name, d.args, d.pending ? "pending" : "running");
          },
          onToolEnd: (d) => {
            turn.step(d.id, d.name, null, d.denied ? "denied" : d.redirected ? "redirected" : d.error ? "error" : "done");
            turn.thinking();
          },
          onSources: (items) => turn.sources(items),
          onDone: (text, data) => {
            if (data.denied) turn.text("Okay, I did not perform that action.", false);
            else if (text) turn.text(text, false);
            end(true);
          },
          onError: (message) => { turn.note(message, "is-error"); end(false); },
          onStopped: () => { turn.note("Stopped", "is-muted"); end(false); },
          onPaused: (questions) => {
            turn.stopThinking();
            turn.endSegment();
            current = null;
            approvalCard(runId, questions, turn).then(resolve);
          },
        });
        current = { runId, close };
      });
    }

    function approvalCard(runId, questions, turn = createTurn(runId)) {
      return new Promise((resolve) => {
        const card = el("div", "hg-card");
        const head = card.appendChild(el("div", "hg-card-head"));
        head.append(el("span", "hg-card-badge", "Approval needed"), el("span", "hg-card-sub", "The assistant wants to change data"));
        const answers = {};
        const status = el("div", "hg-card-status");

        const lock = () => card.querySelectorAll("button, input").forEach((n) => (n.disabled = true));
        // While the card waits, Stop cancels the paused run.
        current = { runId, close() {}, cancel: () => { lock(); status.textContent = "Stopped"; current = null; turn.finish(runId, false); resolve(); } };

        const submit = async () => {
          if (Object.keys(answers).length < questions.length) return;
          current = null;
          lock();
          status.textContent = "Sending…";
          try {
            const res = await client.resume(runId, answers);
            status.textContent = "";
            await follow(runId, res.stream_url, turn);
          } catch (err) {
            status.textContent = err.message;
            turn.finish(runId, false);
          }
          resolve();
        };

        questions.forEach((q) => {
          const row = card.appendChild(el("div", "hg-q"));
          row.appendChild(el("pre", "hg-q-prompt", q.prompt));
          const acts = row.appendChild(el("div", "hg-q-actions"));
          const choose = (answer, label, kind) => {
            answers[q.key] = answer;
            acts.replaceChildren(el("span", `hg-q-chosen ${kind}`, label));
            submit();
          };
          const approve = acts.appendChild(el("button", "hg-btn hg-approve", "Approve"));
          approve.type = "button";
          approve.onclick = () => choose("approve", "Approved", "is-ok");
          const deny = acts.appendChild(el("button", "hg-btn hg-deny", "Deny"));
          deny.type = "button";
          deny.onclick = () => choose("deny", "Denied", "is-bad");
          const other = acts.appendChild(el("input", "hg-input-sm"));
          other.placeholder = "Or describe what to change, then press Enter";
          other.maxLength = 2000;
          other.setAttribute("aria-label", "Feedback instead of approving");
          other.addEventListener("keydown", (e) => {
            if (e.key === "Enter" && other.value.trim()) { e.preventDefault(); choose(other.value.trim(), "Changed: " + other.value.trim(), ""); }
          });
        });
        card.appendChild(status);
        turn.body.appendChild(card);
        list.scrollTop = list.scrollHeight;
      });
    }

    // ---- history ------------------------------------------------------------------------

    function renderHistory(items) {
      const turns = new Map();
      let last = null;
      let ended = null; // the previous user message's run, if it failed or was stopped
      // Show how a run ended under its answer (or alone, if it produced none), like it looked live.
      const flush = () => {
        if (!ended) return;
        const turn = turns.get(ended.run_id) || createTurn(ended.run_id);
        turns.set(ended.run_id, turn);
        if (ended.run_status === "failed") turn.note(ended.run_error || "The assistant failed to respond.", "is-error");
        else turn.note("Stopped", "is-muted");
        ended = null;
      };
      items.forEach((m) => {
        if (m.role === "user") {
          flush();
          addUser(m.content);
          last = null;
          if (m.run_status === "failed" || m.run_status === "stopped") ended = m;
          return;
        }
        if (m.role !== "assistant") return;
        const key = m.run_id || `m${m.id}`;
        const turn = last && last.key === key ? last.turn : createTurn(m.run_id);
        if (!turns.has(key)) turns.set(key, turn);
        last = { key, turn };
        if (m.content) { turn.text(m.content, false); turn.endSegment(); }
        (m.tool_calls || []).forEach((c) => turn.step(c.id, c.function?.name, c.function?.arguments, "done"));
      });
      flush();
      turns.forEach((turn) => turn.finish(turn.row.dataset.runId, false));
    }

    // ---- public API ---------------------------------------------------------------------

    async function send(threadId, content) {
      setBusy(true);
      try {
        addUser(content);
        const run = await client.send(threadId, content);
        await follow(run.run_id, run.stream_url);
      } catch (err) {
        const turn = createTurn(null);
        turn.note(err.message, "is-error");
        turn.finish(null, false);
      } finally {
        setBusy(false);
      }
    }

    async function load(threadId, emptyContent) {
      current?.close();
      current = null;
      const [{ items }, detail] = await Promise.all([client.messages(threadId), client.thread(threadId)]);
      list.replaceChildren();
      renderHistory(items);
      list.scrollTop = list.scrollHeight;
      const active = detail.active_run;
      if (!items.length && !active) notice(emptyContent);
      if (!active) return;
      setBusy(true); // server truth: works across tabs, devices and page reloads
      try {
        const existing = list.querySelector(`.hg-row-ai[data-run-id="${CSS.escape(active.id)}"]`);
        if (active.status === "paused") {
          // Keep what the run already said; the card goes underneath it.
          const turn = createTurn(active.id, existing);
          await approvalCard(active.id, active.questions, turn);
        } else {
          const turn = createTurn(active.id);
          // A stream from the start replays every event, so drop the partial copy from history.
          if (existing && !active.stream_url.includes("after=")) existing.replaceWith(turn.row);
          await follow(active.id, active.stream_url, turn);
        }
      } finally {
        setBusy(false);
      }
    }

    async function stop() {
      if (!current) return;
      const target = current;
      try { await client.stop(target.runId); } catch { /* run already finished */ }
      target.cancel?.();
    }

    return { send, load, stop, notice, isBusy: () => busy };
  }

  window.HelloGuy = { ...(window.HelloGuy || {}), createChatView, toolLabel, toolTitle };
})();
