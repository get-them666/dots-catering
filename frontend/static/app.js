// Becks Catering — public site (quote wizard + gallery + deposit banner)
const API = ""; // served same-origin by FastAPI

async function api(path, options = {}) {
  const resp = await fetch(API + path, options);
  if (!resp.ok) {
    let detail = `${path} failed (${resp.status})`;
    try {
      const body = await resp.json();
      if (body.detail) detail = body.detail;
    } catch (_) {}
    throw new Error(detail);
  }
  return resp.json();
}

// ---------------------------------------------------------------------------
// Quote wizard — 3 steps + live estimate
// ---------------------------------------------------------------------------
const quoteForm = document.getElementById("quoteForm");
const quoteResult = document.getElementById("quoteResult");
const wizardSteps = document.querySelectorAll("#wizardSteps li");
const progressFill = document.getElementById("progressFill");
let wizardStep = 1;

const STYLE_PRICES = {
  "Drop-off Buffet": 18,
  "Family-Style": 25,
  "Cocktail Stations": 28,
  "Plated Dinner": 38,
};

function setWizardStep(n) {
  wizardStep = n;
  document.querySelectorAll(".step").forEach((fs) => {
    fs.hidden = Number(fs.dataset.step) !== n;
  });
  wizardSteps.forEach((li) => {
    const i = Number(li.dataset.i);
    li.classList.toggle("active", i === n);
    li.classList.toggle("done", i < n);
  });
  progressFill.style.width = (n / 3) * 100 + "%";
  quoteResult.textContent = "";
}

function currentStepValid() {
  const fs = document.querySelector(`.step[data-step="${wizardStep}"]`);
  for (const el of fs.querySelectorAll("input, select, textarea")) {
    if (el.hasAttribute("required") && !el.value.trim()) {
      el.focus();
      quoteResult.textContent = "Hold up — " + (el.labels[0] ? el.labels[0].textContent.trim() : "that field") + " is required.";
      return false;
    }
  }
  return true;
}

function currentEstimate() {
  const guests = parseInt(quoteForm.elements["guests"].value, 10);
  const style = quoteForm.elements["service_style"].value;
  if (!guests || guests < 1 || !STYLE_PRICES[style]) return null;
  return Math.round(guests * STYLE_PRICES[style]);
}

function updateEstimate() {
  const box = document.getElementById("estimateBox");
  const est = currentEstimate();
  const guests = parseInt(quoteForm.elements["guests"].value, 10);
  const style = quoteForm.elements["service_style"].value;
  if (!est) {
    box.textContent = "Pick a guest count & style for a live ballpark.";
    return;
  }
  box.innerHTML = `<strong>Ballpark: $${est.toLocaleString()}</strong>
    <span class="muted">($${STYLE_PRICES[style]} × ${guests} guests — plus tax & service. The real number comes after a quick chat.)</span>`;
}

quoteForm.querySelectorAll(".next-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    if (currentStepValid()) setWizardStep(wizardStep + 1);
  });
});
quoteForm.querySelectorAll(".prev-btn").forEach((btn) => {
  btn.addEventListener("click", () => setWizardStep(wizardStep - 1));
});
quoteForm.elements["guests"].addEventListener("input", updateEstimate);
quoteForm.elements["service_style"].addEventListener("change", updateEstimate);
quoteForm.elements["event_date"].addEventListener("change", () => {
  checkAvailability();
  loadWeather();
});

async function checkAvailability() {
  const box = document.getElementById("availability");
  const date = quoteForm.elements["event_date"].value;
  if (!date) {
    box.textContent = "";
    box.className = "avail";
    return;
  }
  try {
    const a = await api("/availability?date=" + encodeURIComponent(date));
    if (a.status === "available") {
      box.textContent = "✅ That date's open — lock it in!";
      box.className = "avail avail-ok";
    } else if (a.status === "limited") {
      box.textContent = "⚠️ Crew's already working that day — we may need to shuffle. Still send it and we'll make it work.";
      box.className = "avail avail-warn";
    } else {
      box.textContent = "❌ Already booked that day — try another date, or ask about a second crew.";
      box.className = "avail avail-bad";
    }
  } catch (_) {
    box.textContent = "";
    box.className = "avail";
  }
}

async function loadWeather() {
  const box = document.getElementById("weatherBox");
  const date = quoteForm.elements["event_date"].value;
  if (!date) {
    box.textContent = "";
    box.className = "weather";
    return;
  }
  try {
    const w = await api("/weather?date=" + encodeURIComponent(date));
    if (!w.ok) {
      box.textContent = "";
      box.className = "weather";
      return;
    }
    const temp = `${w.temp_min}°–${w.temp_max}°F`;
    const prob = w.precip_prob != null ? ` · ${w.precip_prob}% chance of rain` : "";
    const src = w.source === "forecast" ? "7-day forecast" : "typical for this date";
    box.innerHTML = `${w.icon} ${w.label} · ${temp}${prob} <span class="muted">(${src}, ${w.location})</span>`;
    box.className = "weather weather-on";
  } catch (_) {
    box.textContent = "";
    box.className = "weather";
  }
}

const yearBtn = document.getElementById("yearBtn");
const yearGrid = document.getElementById("yearGrid");
if (yearBtn) {
  yearBtn.addEventListener("click", async () => {
    if (!yearGrid.dataset.loaded) {
      try {
        const n = await api("/weather/normals");
        if (!n.ok || !n.months.length) {
          yearGrid.textContent = "Yearly averages aren't available right now.";
          return;
        }
        yearGrid.innerHTML = n.months.map((m) =>
          `<div class="month"><span>${m.label}</span><strong>${m.high}°</strong><span class="muted">low ${m.low}°</span></div>`
        ).join("");
        yearGrid.dataset.loaded = "1";
      } catch (_) {
        yearGrid.textContent = "Yearly averages aren't available right now.";
        return;
      }
    }
    yearGrid.hidden = !yearGrid.hidden;
    yearBtn.textContent = yearGrid.hidden ? "Peek the full year ☀️" : "Hide the full year";
  });
}

quoteForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!currentStepValid()) return;
  const est = currentEstimate();
  const payload = {
    name: quoteForm.elements["name"].value.trim(),
    email: quoteForm.elements["email"].value.trim(),
    phone: quoteForm.elements["phone"].value.trim(),
    event_type: quoteForm.elements["event_type"].value,
    event_date: quoteForm.elements["event_date"].value,
    guests: parseInt(quoteForm.elements["guests"].value, 10),
    service_style: quoteForm.elements["service_style"].value,
    details: quoteForm.elements["details"].value.trim(),
    estimated: est,
  };
  const btn = quoteForm.querySelector('button[type="submit"]');
  if (btn) { btn.disabled = true; btn.textContent = "Sending…"; }
  try {
    await api("/leads", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    quoteResult.textContent = "🌿 In our inbox, " + payload.name.split(" ")[0] + "! Your date is noted — expect a real quote within one business day.";
    quoteForm.reset();
    document.getElementById("estimateBox").textContent = "Pick a guest count & style for a live ballpark.";
    document.getElementById("availability").textContent = "";
    document.getElementById("availability").className = "avail";
    setWizardStep(1);
  } catch (err) {
    quoteResult.textContent = "Error: " + err.message;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "Send My Quote Request 🌿"; }
  }
});

// ---------------------------------------------------------------------------
// Gallery — past events advertise the next ones (view only)
// ---------------------------------------------------------------------------
async function loadGallery() {
  try {
    const galleries = await api("/gallery");
    const grid = document.getElementById("galleryGrid");
    grid.innerHTML = galleries.length
      ? galleries.map((g) =>
          `<div class="gallery-event">
             <h3>${g.event_name} <span class="muted">${[g.event_date, g.guest_count ? `${g.guest_count} guests` : ""].filter(Boolean).join(" · ")}</span></h3>
             <div class="gallery-grid">
               ${g.images.map((src) => `<img src="${API}${src}" alt="${g.event_name}" onclick="window.open('${API}${src}')" />`).join("")}
             </div>
           </div>`
        ).join("")
      : "<p>No past events yet — we're just getting warmed up. Check back soon.</p>";
  } catch (err) {
    document.getElementById("galleryGrid").innerHTML = "<p>Gallery error: " + err.message + "</p>";
  }
}

// ---------------------------------------------------------------------------
// Venues — public directory ("Venues we work with")
// ---------------------------------------------------------------------------
async function loadVenues() {
  const grid = document.getElementById("venuesGrid");
  if (!grid) return;
  try {
    const venues = await api("/venues");
    grid.innerHTML = venues.length
      ? venues.map((v) =>
          `<div class="venue-card">
             <h3>${v.name}</h3>
             <p class="muted">${[v.area, v.venue_type, v.capacity ? `${v.capacity} guests` : ""].filter(Boolean).join(" · ")}</p>
             ${v.price_range ? `<p class="venue-range">${v.price_range}</p>` : ""}
             ${v.website ? `<a href="${v.website}" target="_blank" rel="noopener">Visit site ↗</a>` : ""}
           </div>`
        ).join("")
      : "<p>More venues coming soon — we've got favorites we'd love to share.</p>";
  } catch (err) {
    grid.innerHTML = "<p>Venue list unavailable.</p>";
  }
}

// ---------------------------------------------------------------------------
// Deposit toast banner (fired from Stripe redirect)
// ---------------------------------------------------------------------------
async function showDepositBanner() {
  const params = new URLSearchParams(location.search);
  const banner = document.getElementById("banner");
  const status = params.get("deposit");
  if (status === "cancelled") {
    banner.textContent = "Deposit cancelled — no charge. Your date is still on hold until we talk.";
    banner.className = "banner banner-warn";
    banner.hidden = false;
  } else if (status === "success") {
    const sid = params.get("session_id");
    let text = "🎉 Deposit received! Your date is locked — we'll be in touch to finalize the menu.";
    if (sid) {
      try {
        const info = await api("/pay/session?session_id=" + encodeURIComponent(sid));
        text = info.ok
          ? "🎉 Deposit received! Your date is locked — we'll be in touch to finalize the menu."
          : "Payment didn't complete — reach out and we'll sort it.";
      } catch (_) {}
    }
    banner.textContent = text;
    banner.className = "banner banner-ok";
    banner.hidden = false;
  }
  if (status) history.replaceState(null, "", "/");
}

// ---------------------------------------------------------------------------
// Installable app (PWA)
// ---------------------------------------------------------------------------
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  });
}

// ---------------------------------------------------------------------------
// Kick it off
// ---------------------------------------------------------------------------
async function init() {
  loadGallery();
  loadVenues();
  showDepositBanner();
}
init();