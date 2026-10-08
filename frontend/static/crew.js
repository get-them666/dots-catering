// Dots Catering — Crew app (installable PWA): shifts + live chat
const API = "";

async function api(path) {
  const resp = await fetch(API + path);
  if (!resp.ok) throw new Error(`${path} failed (${resp.status})`);
  return resp.json();
}

// ---------------------------------------------------------------------------
// Installable app
// ---------------------------------------------------------------------------
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  });
}

let deferredPrompt = null;
const installBtn = document.getElementById("installBtn");
window.addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault();
  deferredPrompt = e;
  installBtn.hidden = false;
});
installBtn.addEventListener("click", async () => {
  if (!deferredPrompt) return;
  deferredPrompt.prompt();
  await deferredPrompt.userChoice;
  deferredPrompt = null;
  installBtn.hidden = true;
});
window.addEventListener("appinstalled", () => {
  installBtn.hidden = true;
});

// ---------------------------------------------------------------------------
// Shifts (today + upcoming)
// ---------------------------------------------------------------------------
let employeesData = [];

function todayKey() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function empName(id) {
  const emp = employeesData.find((e) => e.id === id);
  return emp ? emp.name : "Unassigned";
}

function shiftRow(ev) {
  const when = (ev.start || "").replace("T", " ");
  const crew = ev.employee_id ? empName(ev.employee_id) : "Unassigned";
  const badge = ev.type === "booking" ? "Booking" : "Shift";
  return `<div class="shift-item">
            <div><strong>${ev.title || "Event"}</strong><div class="muted">${when}</div></div>
            <div class="shift-meta"><span class="pill">${badge}</span><span class="muted">${crew}</span></div>
          </div>`;
}

async function loadShifts() {
  const list = document.getElementById("shiftList");
  try {
    const [events, employees] = await Promise.all([api("/events"), api("/employees")]);
    employeesData = employees;
    const today = todayKey();
    const upcoming = events
      .filter((e) => (e.start || "").slice(0, 10) >= today)
      .sort((a, b) => (a.start < b.start ? -1 : 1));
    if (!upcoming.length) {
      list.innerHTML = '<p class="muted">No shifts on the books yet. Enjoy the calm.</p>';
      return;
    }
    const todays = upcoming.filter((e) => e.start.slice(0, 10) === today);
    const later = upcoming.filter((e) => e.start.slice(0, 10) !== today);
    let html = "";
    if (todays.length) html += '<h3 class="shift-day">Today</h3>' + todays.map(shiftRow).join("");
    if (later.length) html += '<h3 class="shift-day">Coming up</h3>' + later.map(shiftRow).join("");
    list.innerHTML = html;
  } catch (err) {
    list.innerHTML = `<p class="muted">Could not load shifts (${err.message}).</p>`;
  }
}

// ---------------------------------------------------------------------------
// Crew chat
// ---------------------------------------------------------------------------
const chatLog = document.getElementById("chatLog");
const chatInput = document.getElementById("chatInput");
const sendBtn = document.getElementById("sendBtn");
let ws;

function initWebSocket() {
  const proto = location.protocol === "https:" ? "wss://" : "ws://";
  ws = new WebSocket(proto + location.host + "/ws");
  ws.onmessage = (event) => {
    const msg = document.createElement("div");
    msg.textContent = event.data;
    chatLog.appendChild(msg);
    chatLog.scrollTop = chatLog.scrollHeight;
  };
  ws.onclose = () => setTimeout(initWebSocket, 2000);
}
initWebSocket();

sendBtn.addEventListener("click", () => {
  const text = chatInput.value.trim();
  if (text && ws && ws.readyState === WebSocket.OPEN) {
    ws.send(text);
    chatInput.value = "";
  }
});
chatInput.addEventListener("keypress", (e) => {
  if (e.key === "Enter") { e.preventDefault(); sendBtn.click(); }
});

// ---------------------------------------------------------------------------
// Online / offline
// ---------------------------------------------------------------------------
function netStatus() {
  const banner = document.getElementById("crewBanner");
  if (!navigator.onLine) {
    banner.textContent = "Offline — showing the app shell. Chat resumes when you reconnect.";
    banner.hidden = false;
  } else {
    banner.hidden = true;
  }
}
window.addEventListener("online", netStatus);
window.addEventListener("offline", netStatus);

loadShifts();
netStatus();
