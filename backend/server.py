import base64
import hmac
import json
import os
import secrets
import sqlite3
import string
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends, UploadFile, File, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, EmailStr, Field
import uvicorn

application = FastAPI(title="Dots Catering API")

app = application

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Paths, uploads, env
# ---------------------------------------------------------------------------
REPO_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = REPO_DIR / "frontend" / "static"
TEMPLATE_DIR = REPO_DIR / "frontend" / "templates"
BASE_DIR = REPO_DIR
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATE_DIR)


def _load_dotenv(path: Path) -> None:
    """Load KEY=VALUE pairs from a .env file (real env vars win). No deps."""
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv(BASE_DIR / ".env")

# ---------------------------------------------------------------------------
# Staff login (signed-cookie session; ADMIN_PASSWORD from env/.env)
# ---------------------------------------------------------------------------
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")
if not ADMIN_PASSWORD:
    ADMIN_PASSWORD = secrets.token_urlsafe(9)
    print("⚠️  ADMIN_PASSWORD not set — generated a temporary one for this run:")
    print(f"    {ADMIN_PASSWORD}")
    print("    Put ADMIN_PASSWORD=... in .env for a stable staff password.")

SIGNED_SECRET = os.environ.get("SIGNED_SECRET") or secrets.token_hex(32)
SESSION_COOKIE = "dots_session"


def _session_token() -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps({"a": 1, "iat": int(time.time())}).encode()
    ).decode().rstrip("=")
    sig = hmac.new(SIGNED_SECRET.encode(), payload.encode(), "sha256").hexdigest()
    return f"{payload}.{sig}"


def _verify_session(value: Optional[str]) -> bool:
    if not value or "." not in value:
        return False
    payload, sig = value.rsplit(".", 1)
    expected = hmac.new(SIGNED_SECRET.encode(), payload.encode(), "sha256").hexdigest()
    if not hmac.compare_digest(sig, expected):
        return False
    try:
        data = json.loads(
            base64.urlsafe_b64decode(payload.encode() + b"=" * (-len(payload) % 4))
        )
    except Exception:
        return False
    return data.get("a") == 1 and data.get("iat", 0) > time.time() - 30 * 24 * 3600


def _is_authed(request: Request) -> bool:
    return _verify_session(request.cookies.get(SESSION_COOKIE))


def require_admin(request: Request):
    if not _is_authed(request):
        raise HTTPException(status_code=401, detail="Staff login required")


# ---------------------------------------------------------------------------
# Database (SQLite, stdlib only — survives restarts)
# ---------------------------------------------------------------------------
DB_PATH = BASE_DIR / "dots.db"
_db_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
_db_conn.row_factory = sqlite3.Row
_db_conn.execute("PRAGMA journal_mode=WAL;")
_db_conn.execute("PRAGMA foreign_keys=ON;")
_db_lock = threading.RLock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL, email TEXT NOT NULL, phone TEXT NOT NULL,
  event_type TEXT NOT NULL, event_date TEXT NOT NULL,
  details TEXT, guests INTEGER, service_style TEXT, estimated REAL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS employees (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL, role TEXT NOT NULL, phone TEXT,
  hourly_rate REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL,
  type TEXT NOT NULL DEFAULT 'shift',
  employee_id INTEGER, hours REAL
);
CREATE TABLE IF NOT EXISTS bookings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL, email TEXT NOT NULL, phone TEXT,
  event_type TEXT NOT NULL, event_date TEXT NOT NULL, event_time TEXT,
  guests INTEGER NOT NULL, price REAL NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'Confirmed',
  deposit_paid INTEGER NOT NULL DEFAULT 0,
  deposit_amount REAL NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS galleries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_name TEXT NOT NULL, event_date TEXT, guest_count INTEGER,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS gallery_images (
  gallery_id INTEGER NOT NULL,
  filename TEXT NOT NULL,
  FOREIGN KEY(gallery_id) REFERENCES galleries(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS venues (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  area TEXT, venue_type TEXT, capacity INTEGER,
  price_range TEXT, contact_name TEXT, contact_email TEXT,
  contact_phone TEXT, website TEXT, notes TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS prices (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item TEXT NOT NULL UNIQUE, price_per_lb REAL NOT NULL, unit TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'sample'
);
CREATE TABLE IF NOT EXISTS price_meta (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  source TEXT NOT NULL DEFAULT 'sample',
  updated TEXT, fetched_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS chat_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  body TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""


def _init_db() -> None:
    with _db_lock:
        _db_conn.executescript(_SCHEMA)
        count = _db_conn.execute("SELECT COUNT(*) AS n FROM prices").fetchone()["n"]
        if count == 0:
            _db_conn.executemany(
                "INSERT INTO prices (item, price_per_lb, unit, source) VALUES (?, ?, ?, ?)",
                [
                    ("Chicken Breast", 2.99, "lb", "sample"),
                    ("Salmon Fillet", 5.49, "lb", "sample"),
                    ("Broccoli", 1.25, "lb", "sample"),
                    ("Rice", 0.99, "lb", "sample"),
                ],
            )
        _db_conn.execute(
            "INSERT OR IGNORE INTO price_meta (id, source, updated, fetched_at) VALUES (1, 'sample', NULL, 0)"
        )
        _db_conn.commit()


def _query(sql: str, params=(), one: bool = False):
    with _db_lock:
        cur = _db_conn.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]
    return (rows[0] if rows else None) if one else rows


def _exec(sql: str, params=()):
    with _db_lock:
        cur = _db_conn.execute(sql, params)
        _db_conn.commit()
        return cur.lastrowid


_init_db()

# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/crew", response_class=HTMLResponse)
async def crew_app(request: Request):
    return templates.TemplateResponse(request, "crew.html")


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    if not _is_authed(request):
        return RedirectResponse(url="/admin/login", status_code=302)
    return templates.TemplateResponse(request, "admin.html")


@app.get("/admin/login", response_class=HTMLResponse)
async def admin_login_page(request: Request, error: Optional[str] = None):
    return templates.TemplateResponse(request, "login.html", {"error": error})


@app.post("/admin/login", response_class=HTMLResponse)
async def admin_login(request: Request):
    form = await request.form()
    password = str(form.get("password", ""))
    if not hmac.compare_digest(password, ADMIN_PASSWORD):
        return templates.TemplateResponse(request, "login.html", {"error": "Wrong password. Try again."})
    resp = RedirectResponse(url="/admin", status_code=302)
    resp.set_cookie(SESSION_COOKIE, _session_token(), httponly=True, samesite="lax", max_age=30 * 24 * 3600)
    return resp


@app.post("/admin/logout", response_class=HTMLResponse)
async def admin_logout():
    resp = RedirectResponse(url="/admin/login", status_code=302)
    resp.delete_cookie(SESSION_COOKIE)
    return resp


@app.get("/manifest.webmanifest")
async def manifest():
    return FileResponse(
        STATIC_DIR / "manifest.webmanifest",
        media_type="application/manifest+json",
    )


@app.get("/sw.js")
async def service_worker():
    return FileResponse(
        STATIC_DIR / "sw.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"},
    )


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class Lead(BaseModel):
    name: str = Field(..., example="John Doe")
    email: EmailStr = Field(..., example="john@example.com")
    phone: str = Field(..., example="+1-555-123-4567")
    event_type: str = Field(..., example="Wedding")
    event_date: str = Field(..., example="2024-06-15")
    details: Optional[str] = Field(None, example="Need vegan options")
    guests: Optional[int] = Field(None, gt=0, example=120)
    service_style: Optional[str] = Field(None, example="Plated Dinner")
    estimated: Optional[float] = Field(None, example=4560.0)


class PriceItem(BaseModel):
    item: str
    price_per_lb: float
    unit: Optional[str] = "lb"


class Employee(BaseModel):
    name: str = Field(..., example="Jane Cook")
    role: str = Field(..., example="Line Cook")
    phone: Optional[str] = None
    hourly_rate: float = Field(..., gt=0, example=18.5)


class CalendarEvent(BaseModel):
    title: str = Field(..., example="Wedding service shift")
    start: str = Field(..., example="2026-10-03T16:00")
    end: str = Field(..., example="2026-10-03T22:00")
    type: str = Field(..., example="shift")  # "shift" | "booking"
    employee_id: Optional[int] = None
    hours: Optional[float] = None


class AssignRequest(BaseModel):
    employee_id: int


class Booking(BaseModel):
    name: str = Field(..., example="Sarah & Mike")
    email: EmailStr = Field(..., example="sarah@example.com")
    phone: Optional[str] = None
    event_type: str = Field(..., example="Wedding")
    event_date: str = Field(..., example="2026-10-03")
    event_time: Optional[str] = Field(None, example="17:00")
    guests: int = Field(..., gt=0, example=120)
    price: float = Field(0.0, example=4500.0)
    status: str = "Confirmed"


class CheckoutRequest(BaseModel):
    booking_id: int


class Venue(BaseModel):
    name: str
    area: Optional[str] = Field(None, example="Baltimore / Eastern Shore")
    venue_type: Optional[str] = Field(None, example="Barn")
    capacity: Optional[int] = Field(None, gt=0, example=150)
    price_range: Optional[str] = Field(None, example="$3k–$8k")
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    website: Optional[str] = None
    notes: Optional[str] = None


def event_hours(ev: Dict) -> float:
    if ev.get("hours"):
        return float(ev["hours"])
    start = datetime.fromisoformat(ev["start"])
    end = datetime.fromisoformat(ev["end"])
    return max((end - start).total_seconds() / 3600.0, 0.0)


# ---------------------------------------------------------------------------
# Leads
# ---------------------------------------------------------------------------
@app.post("/leads", response_model=Dict)
async def create_lead(lead: Lead):
    row_id = _exec(
        "INSERT INTO leads (name, email, phone, event_type, event_date, details, guests, service_style, estimated, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            lead.name, lead.email, lead.phone, lead.event_type, lead.event_date,
            lead.details, lead.guests, lead.service_style, lead.estimated,
            datetime.utcnow().isoformat(timespec="seconds") + "Z",
        ),
    )
    return {"id": row_id, **lead.dict()}


@app.get("/leads", response_model=List[Dict], dependencies=[Depends(require_admin)])
async def list_leads():
    return _query("SELECT * FROM leads ORDER BY id DESC")


# ---------------------------------------------------------------------------
# Supplier prices — real feed from USDA AMS MyMarketNews (MARS) when configured
#   USDA_API_KEY     : free key from https://mymarketnews.ams.usda.gov (My Profile)
#   USDA_REPORT_SLUG : report id/slug to pull, e.g. a terminal-market report
# Without these we serve seeded/manual prices so the site always works.
# ---------------------------------------------------------------------------
USDA_API_KEY = os.environ.get("USDA_API_KEY")
USDA_REPORT_SLUG = os.environ.get("USDA_REPORT_SLUG", "")
USDA_BASE = "https://marsapi.ams.usda.gov/services/v1.2"
PRICE_TTL_SECONDS = int(os.environ.get("PRICE_TTL_SECONDS", "1800"))


def _usda_get(path: str):
    token = base64.b64encode(f"{USDA_API_KEY}:".encode()).decode()
    req = urllib.request.Request(
        f"{USDA_BASE}{path}",
        headers={"Authorization": f"Basic {token}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


def _to_float(value):
    if value is None:
        return None
    try:
        return float(str(value).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _first(rec: Dict, keys: List[str]):
    for key in keys:
        val = rec.get(key)
        if val not in (None, "", "N/A", "NA"):
            return val
    return None


def _extract_usda_records(data) -> List[Dict]:
    """MARS responses nest differently per report; walk for price-bearing rows."""
    found: List[Dict] = []

    def walk(node):
        if isinstance(node, dict):
            keys = {k.lower() for k in node.keys()}
            has_name = any(k in keys for k in ("commodity", "item", "commodity_name", "description"))
            has_price = any("price" in k for k in keys)
            if has_name and has_price:
                found.append(node)
            else:
                for val in node.values():
                    walk(val)
        elif isinstance(node, list):
            for val in node:
                walk(val)

    walk(data)
    return found


def _normalize_usda(records: List[Dict]) -> List[Dict]:
    items = []
    for rec in records:
        name = _first(rec, ["commodity", "item", "commodity_name", "description"])
        price = _to_float(_first(rec, ["avg_price", "average_price", "wtd_avg_price", "price"]))
        if price is None:
            low = _to_float(_first(rec, ["low_price", "min_price"]))
            high = _to_float(_first(rec, ["high_price", "max_price"]))
            if low is not None and high is not None:
                price = round((low + high) / 2, 2)
            elif low is not None:
                price = low
        if not name or price is None or price <= 0:
            continue
        unit = _first(rec, ["unit", "unit_of_issue", "package", "units"])
        unit_label = str(unit).lower() if unit else "lb"
        items.append({
            "item": str(name).strip().title(),
            "price_per_lb": round(price, 2),
            "unit": "lb" if ("lb" in unit_label or "pound" in unit_label) else unit_label,
            "source": "USDA",
        })
    # de-dupe by item name, keep first
    seen, unique = set(), []
    for it in items:
        key = it["item"].lower()
        if key not in seen:
            seen.add(key)
            unique.append(it)
    return unique[:60]


def refresh_supplier_prices(force: bool = False) -> bool:
    if not (USDA_API_KEY and USDA_REPORT_SLUG):
        return False
    now = time.time()
    meta = _query("SELECT * FROM price_meta WHERE id = 1", one=True) or {}
    if (not force and meta.get("source") == "USDA"
            and (now - float(meta.get("fetched_at") or 0)) < PRICE_TTL_SECONDS):
        return True
    try:
        data = _usda_get(f"/reports/{USDA_REPORT_SLUG}?allSections=true")
        items = _normalize_usda(_extract_usda_records(data))
        if items:
            with _db_lock:
                _db_conn.execute("DELETE FROM prices")
                _db_conn.executemany(
                    "INSERT INTO prices (item, price_per_lb, unit, source) VALUES (?, ?, ?, 'USDA')",
                    [(it["item"], it["price_per_lb"], it["unit"]) for it in items],
                )
                _db_conn.execute(
                    "UPDATE price_meta SET source='USDA', updated=?, fetched_at=? WHERE id=1",
                    (datetime.utcnow().isoformat(timespec="seconds") + "Z", now),
                )
                _db_conn.commit()
            return True
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, TimeoutError):
        return False
    return False


@app.get("/prices", response_model=List[Dict])
async def get_prices():
    refresh_supplier_prices()
    return _query("SELECT item, price_per_lb, unit, source FROM prices")


@app.get("/prices/meta", response_model=Dict)
async def prices_meta():
    rows = _query("SELECT DISTINCT source FROM prices")
    sources = {r["source"] for r in rows}
    source = next(iter(sources)) if len(sources) == 1 else "mixed"
    meta = _query("SELECT * FROM price_meta WHERE id = 1", one=True) or {}
    count = _query("SELECT COUNT(*) AS n FROM prices", one=True)["n"]
    return {
        "configured": bool(USDA_API_KEY and USDA_REPORT_SLUG),
        "source": source,
        "feed": meta.get("source", "sample"),
        "updated": meta.get("updated"),
        "report_slug": USDA_REPORT_SLUG or None,
        "count": count,
    }


@app.post("/prices", response_model=Dict, dependencies=[Depends(require_admin)])
async def set_price(item: PriceItem):
    rec = {"item": item.item, "price_per_lb": item.price_per_lb, "unit": item.unit or "lb"}
    with _db_lock:
        _db_conn.execute(
            "INSERT INTO prices (item, price_per_lb, unit, source) VALUES (?, ?, ?, 'manual') "
            "ON CONFLICT(item) DO UPDATE SET price_per_lb=excluded.price_per_lb, unit=excluded.unit, source='manual'",
            (rec["item"], rec["price_per_lb"], rec["unit"]),
        )
        _db_conn.commit()
    rec["source"] = "manual"
    return rec


@app.post("/prices/refresh", response_model=Dict, dependencies=[Depends(require_admin)])
async def refresh_prices():
    refreshed = refresh_supplier_prices(force=True)
    meta = _query("SELECT * FROM price_meta WHERE id = 1", one=True) or {}
    count = _query("SELECT COUNT(*) AS n FROM prices", one=True)["n"]
    return {"refreshed": refreshed, "source": meta.get("source"), "updated": meta.get("updated"), "count": count}


# ---------------------------------------------------------------------------
# Employees (GET stays public for the crew app; writes are staff-only)
# ---------------------------------------------------------------------------
@app.post("/employees", response_model=Dict, dependencies=[Depends(require_admin)])
async def create_employee(emp: Employee):
    row_id = _exec(
        "INSERT INTO employees (name, role, phone, hourly_rate) VALUES (?, ?, ?, ?)",
        (emp.name, emp.role, emp.phone, emp.hourly_rate),
    )
    return {"id": row_id, **emp.dict()}


@app.get("/employees", response_model=List[Dict])
async def list_employees():
    return _query("SELECT * FROM employees")


# ---------------------------------------------------------------------------
# Calendar events
# ---------------------------------------------------------------------------
@app.post("/events", response_model=Dict, dependencies=[Depends(require_admin)])
async def create_event(ev: CalendarEvent):
    row_id = _exec(
        "INSERT INTO events (title, start, end, type, employee_id, hours) VALUES (?, ?, ?, ?, ?, ?)",
        (ev.title, ev.start, ev.end, ev.type, ev.employee_id, ev.hours),
    )
    return {"id": row_id, **ev.dict()}


@app.get("/events", response_model=List[Dict])
async def list_events():
    events = _query("SELECT * FROM events ORDER BY start")
    for ev in events:
        ev["hours"] = event_hours(ev)
    return events


@app.delete("/events/{event_id}", dependencies=[Depends(require_admin)])
async def delete_event(event_id: int):
    _exec("DELETE FROM events WHERE id = ?", (event_id,))
    return {"deleted": True}


@app.post("/events/{event_id}/assign", response_model=Dict, dependencies=[Depends(require_admin)])
async def assign_employee(event_id: int, req: AssignRequest):
    ev = _query("SELECT * FROM events WHERE id = ?", (event_id,), one=True)
    if not ev:
        raise HTTPException(status_code=404, detail="Event not found")
    emp = _query("SELECT COUNT(*) AS n FROM employees WHERE id = ?", (req.employee_id,), one=True)
    if not emp or not emp["n"]:
        raise HTTPException(status_code=404, detail="Employee not found")
    _exec("UPDATE events SET employee_id = ? WHERE id = ?", (req.employee_id, event_id))
    ev["employee_id"] = req.employee_id
    return ev


# ---------------------------------------------------------------------------
# Bookings (staff-only read; writes too)
# ---------------------------------------------------------------------------
@app.post("/bookings", response_model=Dict, dependencies=[Depends(require_admin)])
async def create_booking(bk: Booking):
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    row_id = _exec(
        "INSERT INTO bookings (name, email, phone, event_type, event_date, event_time, guests, price, status, deposit_paid, deposit_amount, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?)",
        (bk.name, bk.email, bk.phone, bk.event_type, bk.event_date, bk.event_time,
         bk.guests, bk.price, bk.status, now),
    )
    start = f"{bk.event_date}T{bk.event_time or '17:00'}"
    _exec(
        "INSERT INTO events (title, start, end, type, employee_id, hours) VALUES (?, ?, ?, 'booking', NULL, 0)",
        (f"{bk.name} — {bk.event_type}", start, start),
    )
    return {"id": row_id, **bk.dict(), "deposit_paid": False, "deposit_amount": 0.0}


@app.get("/bookings", response_model=List[Dict], dependencies=[Depends(require_admin)])
async def list_bookings():
    rows = _query("SELECT * FROM bookings ORDER BY event_date")
    for r in rows:
        r["deposit_paid"] = bool(r["deposit_paid"])
    return rows


# ---------------------------------------------------------------------------
# Photo gallery (past events → advertise future ones)
# ---------------------------------------------------------------------------
@app.post("/gallery", response_model=Dict, dependencies=[Depends(require_admin)])
async def upload_gallery(
    event_name: str = Form(...),
    event_date: Optional[str] = Form(None),
    guest_count: Optional[int] = Form(None),
    files: List[UploadFile] = File(...),
):
    saved = []
    for f in files:
        suffix = Path(f.filename or "").suffix or ".jpg"
        name = f"{uuid.uuid4().hex}{suffix.lower()}"
        content = await f.read()
        (UPLOAD_DIR / name).write_bytes(content)
        saved.append(f"/uploads/{name}")
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    with _db_lock:
        gid = _db_conn.execute(
            "INSERT INTO galleries (event_name, event_date, guest_count, created_at) VALUES (?, ?, ?, ?)",
            (event_name, event_date, guest_count, now),
        ).lastrowid
        _db_conn.executemany(
            "INSERT INTO gallery_images (gallery_id, filename) VALUES (?, ?)", [(gid, s) for s in saved]
        )
        _db_conn.commit()
    return {"id": gid, "event_name": event_name, "event_date": event_date, "guest_count": guest_count, "images": saved}


@app.get("/gallery", response_model=List[Dict])
async def list_galleries():
    galleries = _query("SELECT * FROM galleries ORDER BY id DESC")
    for g in galleries:
        imgs = _query("SELECT filename FROM gallery_images WHERE gallery_id = ?", (g["id"],))
        g["images"] = [i["filename"] for i in imgs]
    return galleries


@app.delete("/gallery/{gallery_id}", dependencies=[Depends(require_admin)])
async def delete_gallery(gallery_id: int):
    g = _query("SELECT * FROM galleries WHERE id = ?", (gallery_id,), one=True)
    if not g:
        raise HTTPException(status_code=404, detail="Gallery not found")
    imgs = _query("SELECT filename FROM gallery_images WHERE gallery_id = ?", (gallery_id,))
    for i in imgs:
        fname = Path(i["filename"]).name
        (UPLOAD_DIR / fname).unlink(missing_ok=True)
    _exec("DELETE FROM galleries WHERE id = ?", (gallery_id,))
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Venue directory (public read; staff CRUD) — "Venues we work with"
# ---------------------------------------------------------------------------
@app.get("/venues", response_model=List[Dict])
async def list_venues():
    return _query("SELECT * FROM venues ORDER BY name")


@app.post("/venues", response_model=Dict, dependencies=[Depends(require_admin)])
async def create_venue(venue: Venue):
    row_id = _exec(
        "INSERT INTO venues (name, area, venue_type, capacity, price_range, contact_name, "
        "contact_email, contact_phone, website, notes, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (venue.name, venue.area, venue.venue_type, venue.capacity, venue.price_range,
         venue.contact_name, venue.contact_email, venue.contact_phone, venue.website,
         venue.notes, datetime.utcnow().isoformat(timespec="seconds") + "Z"),
    )
    return {"id": row_id, **venue.dict()}


@app.put("/venues/{venue_id}", response_model=Dict, dependencies=[Depends(require_admin)])
async def update_venue(venue_id: int, venue: Venue):
    if not _query("SELECT id FROM venues WHERE id = ?", (venue_id,), one=True):
        raise HTTPException(status_code=404, detail="Venue not found")
    _exec(
        "UPDATE venues SET name=?, area=?, venue_type=?, capacity=?, price_range=?, contact_name=?, "
        "contact_email=?, contact_phone=?, website=?, notes=? WHERE id=?",
        (venue.name, venue.area, venue.venue_type, venue.capacity, venue.price_range,
         venue.contact_name, venue.contact_email, venue.contact_phone, venue.website,
         venue.notes, venue_id),
    )
    return _query("SELECT * FROM venues WHERE id = ?", (venue_id,), one=True)


@app.delete("/venues/{venue_id}", dependencies=[Depends(require_admin)])
async def delete_venue(venue_id: int):
    _exec("DELETE FROM venues WHERE id = ?", (venue_id,))
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Payroll & accounting (staff-only)
# ---------------------------------------------------------------------------
@app.get("/payroll", response_model=List[Dict], dependencies=[Depends(require_admin)])
async def payroll():
    employees = _query("SELECT * FROM employees")
    events = _query("SELECT * FROM events")
    rows = []
    for emp in employees:
        hours = sum(
            event_hours(ev)
            for ev in events
            if ev.get("type") == "shift" and ev.get("employee_id") == emp["id"]
        )
        gross = round(hours * emp["hourly_rate"], 2)
        rows.append({"employee_id": emp["id"], "name": emp["name"],
                     "role": emp["role"], "hours": round(hours, 2),
                     "rate": emp["hourly_rate"], "gross": gross})
    return rows


@app.get("/accounting", response_model=Dict, dependencies=[Depends(require_admin)])
async def accounting():
    bookings = _query("SELECT * FROM bookings")
    revenue = round(sum(float(b["price"]) for b in bookings), 2)
    rows = await payroll()
    payroll_total = round(sum(r["gross"] for r in rows), 2)
    deposits_collected = round(
        sum(float(b.get("deposit_amount") or 0) for b in bookings if b.get("deposit_paid")), 2
    )
    deposits_outstanding = round(
        sum(
            round(float(b.get("price") or 0) * DEPOSIT_RATIO, 2)
            for b in bookings
            if not b.get("deposit_paid") and float(b.get("price") or 0) > 0
        ),
        2,
    )
    return {
        "revenue": revenue,
        "payroll_total": payroll_total,
        "net": round(revenue - payroll_total, 2),
        "booking_count": len(bookings),
        "shift_hours": round(sum(r["hours"] for r in rows), 2),
        "deposits_collected": deposits_collected,
        "deposits_outstanding": deposits_outstanding,
    }


@app.get("/availability", response_model=Dict)
async def availability(date: str):
    day = date[:10]
    day_bookings = _query(
        "SELECT COUNT(*) AS n FROM bookings WHERE substr(event_date, 1, 10) = ?", (day,), one=True
    )["n"]
    day_shifts = _query(
        "SELECT COUNT(*) AS n FROM events WHERE substr(start, 1, 10) = ?", (day,), one=True
    )["n"]
    if day_bookings:
        status = "booked"
    elif day_shifts:
        status = "limited"
    else:
        status = "available"
    return {"date": day, "status": status, "bookings": day_bookings, "shifts": day_shifts}


# ---------------------------------------------------------------------------
# Weather — Open-Meteo (no API key). 7-day forecast when the date is in range,
# climate "typical" otherwise. Default service area: Virginia Beach, VA
# (covers a 120-mile radius into SE VA, NE NC, and the Eastern Shore).
#   WEATHER_LAT / WEATHER_LON / WEATHER_LOCATION override in .env
# ---------------------------------------------------------------------------
WEATHER_LAT = os.environ.get("WEATHER_LAT")
WEATHER_LON = os.environ.get("WEATHER_LON")
WEATHER_LOCATION = os.environ.get("WEATHER_LOCATION", "Virginia Beach, VA")
_weather_cache: Dict[str, Dict] = {}
_WEATHER_TTL = 6 * 3600
_CLIMATE_YEAR = 2023

_WMO_LABELS = {
    0: ("Clear skies", "☀️"), 1: ("Mostly clear", "🌤️"), 2: ("Partly cloudy", "⛅"),
    3: ("Overcast", "☁️"), 45: ("Foggy", "🌫️"), 48: ("Icy fog", "🌫️"),
    51: ("Light drizzle", "🌦️"), 53: ("Drizzle", "🌦️"), 55: ("Drizzle", "🌧️"),
    56: ("Freezing drizzle", "🌧️"), 57: ("Freezing drizzle", "🌧️"),
    61: ("Light rain", "🌧️"), 63: ("Rain", "🌧️"), 65: ("Heavy rain", "🌧️"),
    66: ("Freezing rain", "🌧️"), 67: ("Freezing rain", "🌧️"),
    71: ("Light snow", "🌨️"), 73: ("Snow", "🌨️"), 75: ("Heavy snow", "❄️"),
    77: ("Snow grains", "🌨️"), 80: ("Light showers", "🌦️"), 81: ("Showers", "🌧️"),
    82: ("Heavy showers", "⛈️"), 85: ("Snow showers", "🌨️"), 86: ("Heavy snow showers", "❄️"),
    95: ("Thunderstorm", "⛈️"), 96: ("Storm w/ hail", "⛈️"), 99: ("Storm w/ hail", "⛈️"),
}


def _wmo_label(code):
    return _WMO_LABELS.get(code, ("Weather", "🌡️"))


def _weather_fetch(url: str, cache_key: str, ttl: int):
    now = time.time()
    cached = _weather_cache.get(cache_key)
    if cached and (now - cached["t"]) < ttl:
        return cached["d"]
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        _weather_cache[cache_key] = {"t": now, "d": data}
        return data
    except Exception:
        return None


def _geo_location():
    if WEATHER_LAT and WEATHER_LON:
        try:
            return float(WEATHER_LAT), float(WEATHER_LON)
        except ValueError:
            pass
    data = _weather_fetch(
        "https://geocoding-api.open-meteo.com/v1/search?name="
        + urllib.parse.quote(WEATHER_LOCATION)
        + "&count=1&language=en&format=json",
        f"geo:{WEATHER_LOCATION.lower()}", _WEATHER_TTL,
    )
    if data and data.get("results"):
        r = data["results"][0]
        return float(r["latitude"]), float(r["longitude"])
    return None


def _climate_data(lat: float, lon: float):
    return _weather_fetch(
        "https://climate-api.open-meteo.com/v1/climate?latitude=%.4f&longitude=%.4f"
        "&start_date=%d-01-01&end_date=%d-12-31&temperature_unit=fahrenheit"
        "&daily=temperature_2m_max,temperature_2m_min&timezone=auto"
        % (lat, lon, _CLIMATE_YEAR, _CLIMATE_YEAR),
        f"typical:{lat:.4f}:{lon:.4f}", _WEATHER_TTL,
    )


def _typical_weather(day: str, lat: float, lon: float):
    data = _climate_data(lat, lon)
    if not (data and data.get("daily")):
        return None
    replace = f"{_CLIMATE_YEAR}-{day[5:]}"
    times = data["daily"].get("time", [])
    if replace not in times:
        return None
    i = times.index(replace)
    return {
        "date": day, "source": "typical",
        "temp_max": data["daily"]["temperature_2m_max"][i],
        "temp_min": data["daily"]["temperature_2m_min"][i],
        "precip_prob": None, "label": "Typical weather", "icon": "🌤️",
    }


@app.get("/weather", response_model=Dict)
async def weather_for_date(date: str):
    latlon = _geo_location()
    if not latlon:
        return {"ok": False, "reason": "Couldn't resolve the service area yet"}
    lat, lon = latlon
    try:
        today = datetime.now().date()
        target = datetime.strptime(date[:10], "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=422, detail="date must be YYYY-MM-DD")
    delta = (target - today).days
    if 0 <= delta <= 16:
        data = _weather_fetch(
            "https://api.open-meteo.com/v1/forecast?latitude=%.4f&longitude=%.4f"
            "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max,weathercode"
            "&temperature_unit=fahrenheit&timezone=auto" % (lat, lon),
            f"forecast:{lat:.4f}:{lon:.4f}", 900,
        )
        if data and data.get("daily") and date in data["daily"].get("time", []):
            i = data["daily"]["time"].index(date)
            wmo = data["daily"]["weathercode"][i]
            label, icon = _wmo_label(wmo)
            return {
                "ok": True, "date": date, "location": WEATHER_LOCATION,
                "source": "forecast",
                "temp_max": data["daily"]["temperature_2m_max"][i],
                "temp_min": data["daily"]["temperature_2m_min"][i],
                "precip_prob": data["daily"]["precipitation_probability_max"][i],
                "label": label, "icon": icon,
            }
    typical = _typical_weather(date, lat, lon)
    if typical:
        return {"ok": True, "location": WEATHER_LOCATION, **typical}
    return {"ok": False, "reason": "Weather data unavailable right now"}


@app.get("/weather/normals", response_model=Dict)
async def weather_normals():
    latlon = _geo_location()
    if not latlon:
        return {"ok": False, "reason": "Couldn't resolve the service area yet"}
    data = _climate_data(*latlon)
    if not (data and data.get("daily")):
        return {"ok": False, "reason": "Weather data unavailable right now"}
    times = data["daily"].get("time", [])
    tmax = data["daily"].get("temperature_2m_max", [])
    tmin = data["daily"].get("temperature_2m_min", [])
    per_month: Dict[int, list] = {}
    for i, t in enumerate(times):
        per_month.setdefault(int(t[5:7]), []).append((tmax[i], tmin[i]))
    labels = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    months = []
    for m in range(1, 13):
        vals = per_month.get(m)
        if not vals:
            continue
        highs = [x for x, _ in vals if x is not None]
        lows = [y for _, y in vals if y is not None]
        months.append({
            "month": m, "label": labels[m - 1],
            "high": round(sum(highs) / len(highs), 1) if highs else None,
            "low": round(sum(lows) / len(lows), 1) if lows else None,
        })
    return {"ok": True, "location": WEATHER_LOCATION, "months": months}


# ---------------------------------------------------------------------------
# Staff chat (WebSocket)
# ---------------------------------------------------------------------------
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        # One dead socket must not break chat for everyone else.
        for connection in list(self.active_connections):
            try:
                await connection.send_text(message)
            except Exception:
                self.disconnect(connection)


manager = ConnectionManager()


@app.get("/chat", response_model=List[Dict])
async def chat_history(limit: int = 200):
    """Persisted crew chat — oldest first, for clients that want a REST pull."""
    limit = max(1, min(int(limit), 500))
    rows = _query(
        "SELECT id, name, body, created_at FROM chat_messages ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    rows.reverse()
    return [
        {"id": m["id"], "name": m["name"], "text": m["body"], "at": m["created_at"]}
        for m in rows
    ]


CHAT_MAX_LEN = 2000


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        # On connect, hand back the stored thread so refreshes keep history.
        history = _query(
            "SELECT id, name, body, created_at FROM chat_messages ORDER BY id DESC LIMIT 200"
        )
        history.reverse()
        await websocket.send_text(
            json.dumps(
                {
                    "type": "history",
                    "messages": [
                        {
                            "id": m["id"],
                            "name": m["name"],
                            "text": m["body"],
                            "at": m["created_at"],
                        }
                        for m in history
                    ],
                }
            )
        )
        while True:
            raw = await websocket.receive_text()
            name, text = "Crew", raw
            try:
                payload = json.loads(raw)
                if isinstance(payload, dict):
                    text = str(payload.get("text") or "")
                    name = str(payload.get("name") or "Crew")
            except (json.JSONDecodeError, TypeError):
                pass
            text = text.strip()[:CHAT_MAX_LEN]
            if not text:
                continue
            name = name.strip()[:40] or "Crew"
            at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
            msg_id = _exec(
                "INSERT INTO chat_messages (name, body, created_at) VALUES (?, ?, ?)",
                (name, text, at),
            )
            await manager.broadcast(
                json.dumps({"type": "msg", "id": msg_id, "name": name, "text": text, "at": at})
            )
    except WebSocketDisconnect:
        manager.disconnect(websocket)


# ---------------------------------------------------------------------------
# Stripe deposits — graceful no-op until STRIPE_SECRET_KEY is set
# ---------------------------------------------------------------------------
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")
DEPOSIT_RATIO = float(os.environ.get("DEPOSIT_RATIO", "0.20"))

stripe = None
if STRIPE_SECRET_KEY:
    try:
        import stripe as _stripe
        stripe = _stripe.StripeClient(STRIPE_SECRET_KEY)
    except ImportError:
        stripe = None
STRIPE_CONFIGURED = stripe is not None


@app.get("/stripe-config", response_model=Dict)
async def stripe_config():
    return {"configured": STRIPE_CONFIGURED, "deposit_ratio": DEPOSIT_RATIO}


@app.post("/checkout", response_model=Dict, dependencies=[Depends(require_admin)])
async def create_checkout(req: CheckoutRequest, request: Request):
    if not STRIPE_CONFIGURED:
        raise HTTPException(status_code=501, detail="Stripe payments are not configured yet")
    bk = _query("SELECT * FROM bookings WHERE id = ?", (req.booking_id,), one=True)
    if not bk:
        raise HTTPException(status_code=404, detail="Booking not found")
    price = float(bk.get("price") or 0)
    if price <= 0:
        raise HTTPException(status_code=400, detail="Add a price to this booking before taking a deposit")
    deposit = round(price * DEPOSIT_RATIO, 2)
    rand = "".join(secrets.choice(string.ascii_lowercase) for _ in range(8))
    base = str(request.base_url).rstrip("/")
    session = stripe.checkout.sessions.create(
        mode="payment",
        line_items=[{
            "quantity": 1,
            "price_data": {
                "currency": "usd",
                "unit_amount": int(round(deposit * 100)),
                "product_data": {"name": f"{bk['name']} — {bk['event_type']} deposit"},
            },
        }],
        metadata={"booking_id": str(bk["id"])},
        success_url=f"{base}/?deposit=success&session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{base}/?deposit=cancelled",
        integration_identifier=f"dots-deposit-{rand}",
    )
    return {"url": session.url, "deposit": deposit}


@app.post("/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig = request.headers.get("stripe-signature")
    if STRIPE_WEBHOOK_SECRET and stripe:
        if not sig:
            raise HTTPException(status_code=400, detail="Missing signature")
        try:
            event = stripe.webhooks.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid signature")
    else:
        event = json.loads(payload)
    if event.get("type") in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        session = event["data"]["object"]
        if session.get("payment_status") != "unpaid":
            bid = int(session.get("metadata", {}).get("booking_id", -1))
            bk = _query("SELECT * FROM bookings WHERE id = ?", (bid,), one=True)
            if bk:
                _exec(
                    "UPDATE bookings SET deposit_paid = 1, deposit_amount = ? WHERE id = ?",
                    (round(float(session.get("amount_total", 0)) / 100.0, 2), bid),
                )
    return {"received": True}


@app.get("/pay/session", response_model=Dict)
async def pay_session(session_id: str):
    if not STRIPE_CONFIGURED:
        return {"ok": False, "reason": "unconfigured"}
    sess = stripe.checkout.sessions.retrieve(session_id)
    return {
        "ok": sess.payment_status in ("paid", "no_payment_required"),
        "payment_status": sess.payment_status,
        "booking_id": sess.metadata.get("booking_id"),
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)