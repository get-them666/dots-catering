// Dots Catering — staff back office
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

function fmt(n) {
  return Number(n || 0).toFixed(2);
}

// ---------------------------------------------------------------------------
// Quote leads
// ---------------------------------------------------------------------------
function leadRow(l) {
  return `<tr>
    <td><strong>${l.name}</strong><br /><span class="muted">${l.email}${l.phone ? " · " + l.phone : ""}</span></td>
    <td>${l.event_type}</td>
    <td>${l.event_date}</td>
    <td>${l.guests ? l.guests + " guests" : "—"}</td>
    <td>${l.service_style || "—"}</td>
    <td>${l.estimated ? "$" + Number(l.estimated).toLocaleString() : "—"}</td>
    <td class="muted">${l.details || ""}</td>
    <td class="muted">${(l.created_at || "").slice(0, 16).replace("T", " ")}</td>
  </tr>`;
}

async function loadLeads() {
  const el = document.getElementById("leadsList");
  try {
    const leads = await api("/leads");
    el.innerHTML = leads.length
      ? `<table>
           <thead><tr><th>Who</th><th>Type</th><th>When</th><th>Size</th><th>Style</th><th>Estimate</th><th>Notes</th><th>Received</th></tr></thead>
           <tbody>${leads.map(leadRow).join("")}</tbody>
         </table>`
      : "<p>No quote requests yet — they'll land here the second someone submits the wizard.</p>";
  } catch (err) {
    el.innerHTML = "<p>Leads error: " + err.message + "</p>";
  }
}

// ---------------------------------------------------------------------------
// Prices
// ---------------------------------------------------------------------------
const PRICE_SOURCE_LABELS = { USDA: "USDA Market News", manual: "Manually set", sample: "Sample data", mixed: "Mixed (USDA + manual)" };

async function loadPrices() {
  const ul = document.getElementById("prices");
  const meta = document.getElementById("priceMeta");
  try {
    const [data, info] = await Promise.all([api("/prices"), api("/prices/meta")]);
    ul.innerHTML = "";
    data.forEach((item) => {
      const li = document.createElement("li");
      const unit = item.unit || "lb";
      const name = document.createElement("span");
      name.textContent = item.item;
      const price = document.createElement("strong");
      price.textContent = `$${item.price_per_lb.toFixed(2)} / ${unit}`;
      li.append(name, price);
      ul.appendChild(li);
    });
    if (meta) {
      const label = PRICE_SOURCE_LABELS[info.source] || info.source;
      const when = info.updated ? ` · updated ${info.updated.replace("T", " ").replace("Z", " UTC")}` : "";
      meta.textContent = `Source: ${label}${when} · `;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.id = "priceRefresh";
      btn.textContent = "Refresh";
      btn.addEventListener("click", refreshPrices);
      meta.appendChild(btn);
    }
  } catch (err) {
    ul.innerHTML = `<li>Error loading prices: ${err.message}</li>`;
  }
}

async function refreshPrices() {
  const meta = document.getElementById("priceMeta");
  const btn = document.getElementById("priceRefresh");
  if (btn) { btn.disabled = true; btn.textContent = "Refreshing…"; }
  try {
    await api("/prices/refresh", { method: "POST" });
    await loadPrices();
  } catch (err) {
    if (meta) meta.textContent = `Refresh failed: ${err.message}`;
  }
}

// ---------------------------------------------------------------------------
// Shared state + reloaders
// ---------------------------------------------------------------------------
let employeesData = [];
let eventsData = [];
let bookingsData = [];

function empName(id) {
  const emp = employeesData.find((e) => e.id === id);
  return emp ? emp.name : "—";
}

function reloadAll() {
  // Each loader is independent — one failing endpoint must not take the
  // rest of the back office down with it.
  const safe = (p) => p.catch((err) => console.error("reload failed:", err));
  Promise.all([loadLeads(), loadEmployees(), loadPrices()].map(safe)).then(() =>
    Promise.all([
      loadEvents(), loadBookings(), loadPayroll(),
      loadAccounting(), loadGallery(), loadVenues(),
    ].map(safe))
  );
}

// ---------------------------------------------------------------------------
// Employees
// ---------------------------------------------------------------------------
async function loadEmployees() {
  employeesData = await api("/employees");
  renderEmployeeList();
  renderEmployeeSelects();
}

function renderEmployeeSelects() {
  const evEmp = document.getElementById("evEmp");
  const assignEmployee = document.getElementById("assignEmployee");
  const options = '<option value="">— none —</option>' +
    employeesData.map((e) => `<option value="${e.id}">${e.name}</option>`).join("");
  evEmp.innerHTML = options;
  assignEmployee.innerHTML = options.replace(">— none —<", ">— pick crew —<");
}

function renderEmployeeList() {
  const el = document.getElementById("employeeList");
  el.innerHTML = employeesData.length
    ? `<table>
         <thead><tr><th>Name</th><th>Role</th><th>Rate</th><th>Phone</th></tr></thead>
         <tbody>${employeesData.map((e) =>
           `<tr><td>${e.name}</td><td>${e.role}</td><td>$${e.hourly_rate.toFixed(2)}/hr</td><td>${e.phone || "—"}</td></tr>`
         ).join("")}</tbody>
       </table>`
    : "<p>No crew yet — add your first hire above.</p>";
}

const employeeForm = document.getElementById("employeeForm");
if (employeeForm) {
  employeeForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const payload = Object.fromEntries(new FormData(employeeForm).entries());
    payload.hourly_rate = parseFloat(payload.hourly_rate);
    try {
      await api("/employees", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      employeeForm.reset();
      reloadAll();
    } catch (err) {
      alert(err.message);
    }
  });
}

// ---------------------------------------------------------------------------
// Calendar
// ---------------------------------------------------------------------------
let calYear = new Date().getFullYear();
let calMonth = new Date().getMonth();
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const calLabel = document.getElementById("calLabel");
const calGrid = document.getElementById("calGrid");

function dateKey(ev) {
  return (ev.start || "").slice(0, 10);
}

async function loadEvents() {
  try {
    eventsData = await api("/events");
    renderCalendar();
  } catch (err) {
    const el = document.getElementById("eventList");
    if (el) el.innerHTML = "<p>Calendar error: " + err.message + "</p>";
  }
}

function renderCalendar() {
  calLabel.textContent = `${MONTHS[calMonth]} ${calYear}`;
  const first = new Date(calYear, calMonth, 1);
  const daysInMonth = new Date(calYear, calMonth + 1, 0).getDate();
  const startDow = first.getDay();
  const byDate = {};
  eventsData.forEach((ev) => {
    const k = dateKey(ev);
    (byDate[k] = byDate[k] || []).push(ev);
  });
  calGrid.innerHTML = "";
  ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].forEach((d) => {
    const h = document.createElement("div");
    h.className = "cal-head";
    h.textContent = d;
    calGrid.appendChild(h);
  });
  for (let i = 0; i < startDow; i++) {
    calGrid.appendChild(document.createElement("div"));
  }
  for (let day = 1; day <= daysInMonth; day++) {
    const cell = document.createElement("div");
    cell.className = "cal-cell";
    const k = `${calYear}-${String(calMonth + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
    cell.innerHTML = `<strong>${day}</strong>`;
    (byDate[k] || []).forEach((ev) => {
      const chip = document.createElement("span");
      chip.className = `cal-chip ${ev.type === "shift" ? "chip-shift" : "chip-booking"}`;
      chip.title = (ev.title || "") + (ev.employee_id ? " · " + empName(ev.employee_id) : "");
      chip.textContent = (ev.title || "Event").slice(0, 16);
      cell.appendChild(chip);
    });
    calGrid.appendChild(cell);
  }
  renderEventList();
}

document.getElementById("calPrev").addEventListener("click", () => {
  calMonth--;
  if (calMonth < 0) { calMonth = 11; calYear--; }
  renderCalendar();
});
document.getElementById("calNext").addEventListener("click", () => {
  calMonth++;
  if (calMonth > 11) { calMonth = 0; calYear++; }
  renderCalendar();
});

function renderEventList() {
  const el = document.getElementById("eventList");
  const assignEvent = document.getElementById("assignEvent");
  const monthStr = `${calYear}-${String(calMonth + 1).padStart(2, "0")}`;
  const monthEvents = eventsData
    .filter((ev) => dateKey(ev).startsWith(monthStr))
    .sort((a, b) => (a.start < b.start ? -1 : 1));
  assignEvent.innerHTML = '<option value="">— pick event —</option>' +
    monthEvents.map((ev) =>
      `<option value="${ev.id}">${ev.start.slice(0, 16)} — ${ev.title}</option>`
    ).join("");
  el.innerHTML = monthEvents.length
    ? `<table>
         <thead><tr><th>When</th><th>What</th><th>Crew</th><th></th></tr></thead>
         <tbody>${monthEvents.map((ev) =>
           `<tr>
              <td>${ev.start.replace("T", " ")}</td>
              <td>${ev.title}</td>
              <td>${empName(ev.employee_id)}</td>
              <td><button class="tiny" onclick="deleteEvent(${ev.id})">✕</button></td>
            </tr>`
         ).join("")}</tbody>
       </table>`
    : "<p>No events this month.</p>";
}

async function deleteEvent(id) {
  await api(`/events/${id}`, { method: "DELETE" });
  reloadAll();
}
window.deleteEvent = deleteEvent;

const eventForm = document.getElementById("eventForm");
if (eventForm) {
  eventForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const payload = Object.fromEntries(new FormData(eventForm).entries());
    payload.type = "shift";
    payload.employee_id = payload.employee_id ? parseInt(payload.employee_id, 10) : null;
    try {
      await api("/events", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      eventForm.reset();
      reloadAll();
    } catch (err) {
      alert(err.message);
    }
  });
}

const assignForm = document.getElementById("assignForm");
if (assignForm) {
  assignForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const eventId = document.getElementById("assignEvent").value;
    const employeeId = document.getElementById("assignEmployee").value;
    try {
      await api(`/events/${eventId}/assign`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ employee_id: parseInt(employeeId, 10) }),
      });
      reloadAll();
    } catch (err) {
      alert(err.message);
    }
  });
}

// ---------------------------------------------------------------------------
// Bookings + Stripe deposits
// ---------------------------------------------------------------------------
let stripeConfig = { configured: false, deposit_ratio: 0.2 };

async function loadStripeConfig() {
  try {
    stripeConfig = await api("/stripe-config");
  } catch (_) {
    stripeConfig = { configured: false, deposit_ratio: 0.2 };
  }
}

function depositCell(b) {
  if (!Number(b.price) || Number(b.price) <= 0) return '<span class="muted">—</span>';
  if (b.deposit_paid) {
    const amt = b.deposit_amount ? ` $${fmt(b.deposit_amount)}` : "";
    return `<span class="paid-chip">✓ Paid${amt}</span>`;
  }
  if (!stripeConfig.configured) return '<span class="muted">Coming soon</span>';
  const pct = Math.round((stripeConfig.deposit_ratio || 0.2) * 100);
  return `<button class="tiny deposit-btn" onclick="payDeposit(${b.id})">Pay ${pct}% Deposit</button>`;
}

async function loadBookings() {
  bookingsData = await api("/bookings");
  const el = document.getElementById("bookingList");
  el.innerHTML = bookingsData.length
    ? `<table>
         <thead><tr><th>Date</th><th>Client</th><th>Type</th><th>Guests</th><th>Price</th><th>Deposit</th></tr></thead>
         <tbody>${bookingsData.map((b) =>
           `<tr><td>${b.event_date}</td><td>${b.name}</td><td>${b.event_type}</td>
            <td>${b.guests}</td><td>$${fmt(b.price)}</td><td>${depositCell(b)}</td></tr>`
         ).join("")}</tbody>
       </table>`
    : "<p>No confirmed bookings yet.</p>";
}

async function payDeposit(bookingId) {
  try {
    const res = await api("/checkout", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ booking_id: bookingId }),
    });
    if (res.url) window.location.href = res.url;
  } catch (err) {
    document.getElementById("bookingResult").textContent = "Deposit unavailable: " + err.message;
  }
}
window.payDeposit = payDeposit;

const bookingForm = document.getElementById("bookingForm");
const bookingResult = document.getElementById("bookingResult");
if (bookingForm) {
  bookingForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const payload = Object.fromEntries(new FormData(bookingForm).entries());
    payload.guests = parseInt(payload.guests, 10);
    payload.price = parseFloat(payload.price) || 0;
    try {
      await api("/bookings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      bookingResult.textContent = "Booking confirmed 🎉";
      bookingForm.reset();
      reloadAll();
    } catch (err) {
      bookingResult.textContent = "Error: " + err.message;
    }
  });
}

// ---------------------------------------------------------------------------
// Payroll & accounting
// ---------------------------------------------------------------------------
async function loadPayroll() {
  const rows = await api("/payroll");
  const tbody = document.getElementById("payrollBody");
  tbody.innerHTML = rows.length
    ? rows.map((r) =>
        `<tr><td>${r.name}</td><td>${r.role}</td><td>${r.hours.toFixed(2)}</td>
         <td>$${r.rate.toFixed(2)}</td><td>$${r.gross.toFixed(2)}</td></tr>`
      ).join("")
    : '<tr><td colspan="5">No crew → no payroll</td></tr>';
}

async function loadAccounting() {
  const a = await api("/accounting");
  document.getElementById("acctCards").innerHTML = `
    <div class="card"><div class="num">$${a.revenue.toFixed(2)}</div>Booked revenue</div>
    <div class="card"><div class="num">$${a.deposits_collected.toFixed(2)}</div>Deposits in</div>
    <div class="card"><div class="num">$${a.deposits_outstanding.toFixed(2)}</div>Deposits due</div>
    <div class="card"><div class="num">$${a.payroll_total.toFixed(2)}</div>Crew cost</div>
    <div class="card"><div class="num">$${a.net.toFixed(2)}</div>Net</div>
    <div class="card"><div class="num">${a.booking_count}</div>Bookings</div>
    <div class="card"><div class="num">${a.shift_hours.toFixed(1)}</div>Shift hours</div>`;
}

// ---------------------------------------------------------------------------
// Gallery — upload + delete from the office
// ---------------------------------------------------------------------------
async function loadGallery() {
  try {
    const galleries = await api("/gallery");
    const grid = document.getElementById("galleryGrid");
    grid.innerHTML = galleries.length
      ? galleries.map((g) =>
          `<div class="gallery-event">
             <h3>${g.event_name} <span class="muted">${[g.event_date, g.guest_count ? `${g.guest_count} guests` : ""].filter(Boolean).join(" · ")}</span>
                 <button class="tiny" onclick="deleteGallery(${g.id})">✕ remove</button></h3>
             <div class="gallery-grid">
               ${g.images.map((src) => `<img src="${API}${src}" alt="${g.event_name}" onclick="window.open('${API}${src}')" />`).join("")}
             </div>
           </div>`
        ).join("")
      : "<p>No past events yet — upload the receipts (photos) above.</p>";
  } catch (err) {
    document.getElementById("galleryGrid").innerHTML = "<p>Gallery error: " + err.message + "</p>";
  }
}

async function deleteGallery(id) {
  await api(`/gallery/${id}`, { method: "DELETE" });
  loadGallery();
}
window.deleteGallery = deleteGallery;

const galleryForm = document.getElementById("galleryForm");
const galleryResult = document.getElementById("galleryResult");
if (galleryForm) {
  galleryForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData();
    fd.append("event_name", document.getElementById("gEventName").value);
    fd.append("event_date", document.getElementById("gEventDate").value);
    fd.append("guest_count", document.getElementById("gGuestCount").value || "");
    Array.from(document.getElementById("gFiles").files).forEach((f) => fd.append("files", f));
    try {
      await api("/gallery", { method: "POST", body: fd });
      galleryResult.textContent = "Uploaded! 🎉";
      galleryForm.reset();
      loadGallery();
    } catch (err) {
      galleryResult.textContent = "Upload error: " + err.message;
    }
  });
}

// ---------------------------------------------------------------------------
// Venue directory — CRUD
// ---------------------------------------------------------------------------
let venuesData = [];
let editingVenueId = null;

function venueFormPayload() {
  const form = document.getElementById("venueForm");
  const fd = new FormData(form);
  const p = Object.fromEntries(fd.entries());
  p.capacity = p.capacity ? parseInt(p.capacity, 10) : null;
  return p;
}

function venueFormValues(v) {
  const form = document.getElementById("venueForm");
  ["name", "area", "venue_type", "capacity", "price_range", "contact_name",
   "contact_email", "contact_phone", "website", "notes"].forEach((k) => {
    form.elements[k].value = v ? (v[k] ?? "") : "";
  });
}

const venueForm = document.getElementById("venueForm");
const venueCancelEdit = document.getElementById("venueCancelEdit");
const venueSubmitBtn = document.getElementById("venueSubmitBtn");
if (venueForm) {
  venueForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const payload = venueFormPayload();
      const opts = {
        method: editingVenueId ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      };
      const url = editingVenueId ? `/venues/${editingVenueId}` : "/venues";
      await api(url, opts);
      editingVenueId = null;
      venueCancelEdit.hidden = true;
      venueSubmitBtn.textContent = "Add Venue";
      venueFormValues(null);
      loadVenues();
    } catch (err) {
      alert(err.message);
    }
  });
}
if (venueCancelEdit) {
  venueCancelEdit.addEventListener("click", () => {
    editingVenueId = null;
    venueCancelEdit.hidden = true;
    venueSubmitBtn.textContent = "Add Venue";
    venueFormValues(null);
  });
}

async function loadVenues() {
  const el = document.getElementById("venueList");
  try {
    venuesData = await api("/venues");
    el.innerHTML = venuesData.length
      ? `<table>
           <thead><tr><th>Venue</th><th>Area</th><th>Type</th><th>Capacity</th><th>Price</th><th>Contact</th><th></th><th></th></tr></thead>
           <tbody>${venuesData.map((v) =>
             `<tr>
                <td><strong>${v.name}</strong>${v.website ? ` <a href="${v.website}" target="_blank" rel="noopener">↗</a>` : ""}</td>
                <td>${v.area || "—"}</td><td>${v.venue_type || "—"}</td><td>${v.capacity || "—"}</td>
                <td>${v.price_range || "—"}</td>
                <td>${[v.contact_name, v.contact_email || v.contact_phone].filter(Boolean).join("<br />") || "—"}</td>
                <td><button class="tiny" onclick="editVenue(${v.id})">✎</button></td>
                <td><button class="tiny" onclick="deleteVenue(${v.id})">✕</button></td>
              </tr>`
           ).join("")}</tbody>
         </table>`
      : "<p>No venues yet — add the spots you love to work.</p>";
  } catch (err) {
    el.innerHTML = "<p>Venues error: " + err.message + "</p>";
  }
}

function editVenue(id) {
  const v = venuesData.find((x) => x.id === id);
  if (!v) return;
  editingVenueId = id;
  venueFormValues(v);
  venueSubmitBtn.textContent = "Save Venue";
  venueCancelEdit.hidden = false;
  window.scrollTo({ top: document.getElementById("venues").offsetTop - 80, behavior: "smooth" });
}
window.editVenue = editVenue;

async function deleteVenue(id) {
  await api(`/venues/${id}`, { method: "DELETE" });
  loadVenues();
}
window.deleteVenue = deleteVenue;

// ---------------------------------------------------------------------------
// Crew chat — shared module (chat.js); thread is persisted server-side
// ---------------------------------------------------------------------------
const chatLog = document.getElementById("chatLog");
const chatInput = document.getElementById("chatInput");
const sendBtn = document.getElementById("sendBtn");
initDotsChat({
  name: "Staff",
  logEl: chatLog,
  inputEl: chatInput,
  sendEl: sendBtn,
});

// ---------------------------------------------------------------------------
// Kick it off
// ---------------------------------------------------------------------------
async function init() {
  await loadStripeConfig();
  reloadAll();
  renderCalendar();
}
init();