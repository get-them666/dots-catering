// Dots Catering service worker — installable app shell + offline fallback
const CACHE = "dots-v3";
const SHELL = [
  "/",
  "/crew",
  "/static/style.css",
  "/static/app.js",
  "/static/chat.js",
  "/static/crew.js",
  "/static/icon-192.png",
  "/static/icon-512.png",
  "/static/apple-touch-icon.png",
  "/manifest.webmanifest",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // Navigations: network first, fall back to cached shell (offline)
  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req).catch(() =>
        caches.match(req).then((hit) => hit || caches.match("/crew") || caches.match("/"))
      )
    );
    return;
  }

  // Static assets: cache first, then network (and store)
  const isStatic =
    url.pathname.startsWith("/static/") ||
    url.pathname === "/manifest.webmanifest" ||
    url.pathname === "/sw.js";
  if (isStatic) {
    event.respondWith(
      caches.match(req).then(
        (hit) =>
          hit ||
          fetch(req).then((res) => {
            const copy = res.clone();
            caches.open(CACHE).then((cache) => cache.put(req, copy));
            return res;
          })
      )
    );
    return;
  }

  // Dynamic API calls (events, prices, chat) — always live, never cached.
});
