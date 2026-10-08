// Dots Catering — shared crew chat
// The thread lives in SQLite on the server: history is pushed to every
// socket on connect, and every message is stored before it's broadcast.
// Used by both /crew (crew.js) and /admin (admin.js).

(function () {
  function fmtTime(at) {
    try {
      const d = new Date(at);
      if (isNaN(d.getTime())) return "";
      return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    } catch (_) {
      return "";
    }
  }

  function initDotsChat(opts) {
    const logEl = opts.logEl;
    const inputEl = opts.inputEl;
    const sendEl = opts.sendEl;
    const getName = typeof opts.name === "function" ? opts.name : () => opts.name || "Crew";

    const queue = []; // messages typed while the socket is down
    let ws = null;

    function showPlaceholder() {
      logEl.innerHTML = "";
      const p = document.createElement("p");
      p.className = "muted chat-empty";
      p.textContent = "No messages yet — say hi 👋";
      logEl.appendChild(p);
    }

    function renderMsg(m) {
      const ph = logEl.querySelector(".chat-empty");
      if (ph) ph.remove();
      const row = document.createElement("div");
      row.className = "chat-msg";
      const who = document.createElement("span");
      who.className = "who";
      who.textContent = m.name || "Crew";
      const when = document.createElement("span");
      when.className = "when";
      when.textContent = fmtTime(m.at);
      const txt = document.createElement("span");
      txt.className = "txt";
      txt.textContent = " " + (m.text || "");
      row.append(who, when, txt);
      logEl.appendChild(row);
      logEl.scrollTop = logEl.scrollHeight;
    }

    function renderHistory(messages) {
      if (!messages || !messages.length) {
        showPlaceholder();
        return;
      }
      logEl.innerHTML = "";
      messages.forEach(renderMsg);
      logEl.scrollTop = logEl.scrollHeight;
    }

    function flush() {
      while (queue.length && ws && ws.readyState === WebSocket.OPEN) {
        ws.send(queue.shift());
      }
    }

    function connect() {
      const proto = location.protocol === "https:" ? "wss://" : "ws://";
      ws = new WebSocket(proto + location.host + "/ws");
      ws.onopen = flush;
      ws.onmessage = (event) => {
        let data = null;
        try {
          data = JSON.parse(event.data);
        } catch (_) {
          return; // not ours (e.g. legacy plain-text frame)
        }
        if (!data || typeof data !== "object") return;
        if (data.type === "history") renderHistory(data.messages);
        else if (data.type === "msg") renderMsg(data);
      };
      ws.onclose = () => setTimeout(connect, 2000);
    }

    function send() {
      const text = (inputEl && inputEl.value ? inputEl.value : "").trim();
      if (!text) return;
      queue.push(JSON.stringify({ name: getName(), text: text }));
      if (inputEl) inputEl.value = "";
      flush();
    }

    if (sendEl) sendEl.addEventListener("click", send);
    if (inputEl) {
      inputEl.addEventListener("keypress", (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          send();
        }
      });
    }

    showPlaceholder();
    connect();
    return { send };
  }

  window.initDotsChat = initDotsChat;
})();
