/* Shared chat rendering for the admin widget and the full page:
 * streamed answers, tool activity, approval cards (approve / deny / redirect), stop, feedback.
 * All model and user text is inserted as text nodes, never as HTML.
 */
(function () {
  "use strict";

  function createChatView({ client, list, onBusy = () => {} }) {
    let current = null; // { runId, close }
    let busy = false;

    function setBusy(value) {
      busy = value;
      onBusy(value);
    }

    function scroll() {
      list.scrollTop = list.scrollHeight;
    }

    function el(tag, className, text) {
      const node = document.createElement(tag);
      if (className) node.className = className;
      if (text !== undefined) node.textContent = text;
      return node;
    }

    function addMessage(role, text) {
      list.querySelector(".hg-empty")?.remove();
      const bubble = el("div", `hg-msg ${role === "user" ? "user" : "assistant"}`);
      bubble.appendChild(document.createTextNode(text));
      list.appendChild(bubble);
      scroll();
      return bubble;
    }

    function notice(text) {
      list.replaceChildren(el("p", "hg-empty", text));
    }

    // ---- one run: stream -> (pause -> approve -> stream)* -> done ------------------

    function follow(runId, streamUrl) {
      return new Promise((resolve) => {
        const bubble = addMessage("assistant", "…");
        bubble.classList.add("hg-pending"); // CSS shows a typing indicator until the first token
        const body = bubble.firstChild;
        const meta = bubble.appendChild(el("span", "hg-meta"));
        let sources = "";
        const finish = (note) => {
          bubble.classList.remove("hg-pending");
          if (note !== undefined) meta.textContent = note;
          if (body.textContent === "…" && !meta.textContent) bubble.remove();
          else if (body.textContent === "…") body.textContent = "";
          current = null;
          resolve();
        };
        const close = window.HelloGuy.streamRun(streamUrl, {
          onToken: (text) => { bubble.classList.remove("hg-pending"); body.textContent = text; scroll(); },
          onTool: (name, info) => {
            if (name) meta.textContent = info.pending ? `⏸ ${name} needs approval` : `🔧 ${name}…`;
            else meta.textContent = info.denied ? "✋ denied" : "";
          },
          onSources: (items) => { sources = items.length ? "📄 " + items.map((s) => s.title).join(", ") : ""; },
          onDone: (text, data) => {
            if (data.denied) body.textContent = "Theek hai, maine woh action nahi kiya.";
            else if (text !== undefined) body.textContent = text;
            finish(sources);
            bubble.appendChild(feedbackBar(runId));
          },
          onError: (message) => { bubble.classList.add("hg-error"); finish("⚠ " + message); },
          onStopped: () => finish("⏹ Stopped"),
          onPaused: (questions) => {
            if (body.textContent === "…") bubble.remove();
            current = null;
            approvalCard(runId, questions).then(resolve);
          },
        });
        current = { runId, close };
      });
    }

    function approvalCard(runId, questions) {
      return new Promise((resolve) => {
        const card = el("div", "hg-card");
        card.appendChild(el("div", "hg-card-title", "⏸ Approval needed"));
        const answers = {};
        const status = el("div", "hg-card-status");

        const lock = () => card.querySelectorAll("button, input").forEach((n) => (n.disabled = true));
        // While the card waits, Stop cancels the paused run.
        current = { runId, close() {}, cancel: () => { lock(); status.textContent = "⏹ Stopped"; current = null; resolve(); } };

        const submit = async () => {
          if (Object.keys(answers).length < questions.length) return;
          current = null;
          lock();
          status.textContent = "Sending…";
          try {
            const res = await client.resume(runId, answers);
            status.textContent = "";
            await follow(runId, res.stream_url);
          } catch (err) {
            status.textContent = "⚠ " + err.message;
          }
          resolve();
        };

        questions.forEach((q) => {
          const row = el("div", "hg-q");
          row.appendChild(el("pre", "hg-q-prompt", q.prompt));
          const actions = el("div", "hg-q-actions");
          const choose = (answer, label) => {
            answers[q.key] = answer;
            actions.replaceChildren(el("span", "hg-q-chosen", label));
            submit();
          };
          const approve = el("button", "hg-btn hg-approve", "Approve");
          approve.type = "button";
          approve.onclick = () => choose("approve", "✓ Approved");
          const deny = el("button", "hg-btn hg-deny", "Deny");
          deny.type = "button";
          deny.onclick = () => choose("deny", "✗ Denied");
          const other = el("input", "hg-q-other");
          other.placeholder = "Or tell it what to change…";
          other.maxLength = 2000;
          other.setAttribute("aria-label", "Feedback instead of approving");
          other.addEventListener("keydown", (e) => {
            if (e.key === "Enter" && other.value.trim()) { e.preventDefault(); choose(other.value.trim(), "↩ " + other.value.trim()); }
          });
          actions.append(approve, deny, other);
          row.appendChild(actions);
          card.appendChild(row);
        });
        card.appendChild(status);
        list.appendChild(card);
        scroll();
      });
    }

    function feedbackBar(runId) {
      const bar = el("span", "hg-feedback");
      const send = async (rating, comment = "") => {
        try {
          await client.feedback(runId, rating, comment);
          bar.replaceChildren(el("span", "hg-thanks", rating === "up" ? "👍 Thanks!" : "👎 Noted, thanks!"));
        } catch (err) {
          bar.replaceChildren(el("span", "hg-thanks", "⚠ " + err.message));
        }
      };
      const up = el("button", "hg-fb", "👍");
      up.type = "button";
      up.title = "Good answer";
      up.onclick = () => send("up");
      const down = el("button", "hg-fb", "👎");
      down.type = "button";
      down.title = "Bad answer";
      down.onclick = () => {
        const input = el("input", "hg-q-other");
        input.placeholder = "What went wrong? (Enter to send)";
        input.maxLength = 500;
        input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); send("down", input.value.trim()); } });
        bar.replaceChildren(input);
        input.focus();
      };
      bar.append(up, down);
      return bar;
    }

    // ---- public API --------------------------------------------------------------------

    async function send(threadId, content) {
      setBusy(true);
      try {
        addMessage("user", content);
        const run = await client.send(threadId, content);
        await follow(run.run_id, run.stream_url);
      } catch (err) {
        addMessage("assistant", "⚠ " + err.message).classList.add("hg-error");
      } finally {
        setBusy(false);
      }
    }

    async function load(threadId, emptyText) {
      current?.close();
      current = null;
      const [{ items }, detail] = await Promise.all([client.messages(threadId), client.thread(threadId)]);
      list.replaceChildren();
      items.forEach((m) => addMessage(m.role, m.content));
      if (!items.length) notice(emptyText);
      const active = detail.active_run;
      if (!active) return;
      setBusy(true); // server truth: works across tabs, devices and page reloads
      try {
        if (active.status === "paused") await approvalCard(active.id, active.questions);
        else await follow(active.id, active.stream_url);
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

    return { send, load, stop, notice, isBusy: () => busy, isStreaming: () => Boolean(current) };
  }

  window.HelloGuy = { ...(window.HelloGuy || {}), createChatView };
})();
