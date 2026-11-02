/* Lovci duchů: práce bez signálu.
   Stránka: nejdřív ze sítě (ať je vždy nejnovější), bez sítě z paměti.
   Mapa (mapa/), duchové, ikony a písma: z paměti, když už tam jsou; jinak ze sítě a uložit.
   Mapa ČR (mapa/cr/*.pmtiles) jde mimo: hra z ní čte kousky přes HTTP Range a navštívené dlaždice i stažené
   oblasti si ukládá sama do paměti "lovci-mapa", kterou tu nemažeme. */
var V = "lovci-duchu-v2", MAPA = "lovci-mapa";
var SHELL = ["./", "index.html", "manifest.webmanifest", "icon-192.png", "icon-512.png",
  "duchove/pani.png", "duchove/mnich.png", "duchove/prosteradlo.png", "duchove/stin.png", "duchove/orby.png", "duchove/zombie.png", "duchove/klekanice.png"];
self.addEventListener("install", function (e) {
  e.waitUntil(caches.open(V).then(function (c) { return c.addAll(SHELL); }).catch(function () {}));
  self.skipWaiting();
});
self.addEventListener("activate", function (e) {
  e.waitUntil(caches.keys().then(function (ks) { return Promise.all(ks.filter(function (k) { return k !== V && k !== MAPA; }).map(function (k) { return caches.delete(k); })); }).then(function () { return self.clients.claim(); }));
});
function cacheFirst(req) {
  return caches.open(V).then(function (c) {
    return c.match(req).then(function (hit) {
      if (hit) return hit;
      return fetch(req).then(function (r) { if (r && (r.ok || r.type === "opaque")) c.put(req, r.clone()); return r; });
    });
  });
}
function networkFirst(req) {
  return fetch(req).then(function (r) {
    if (r && r.ok) { var cl = r.clone(); caches.open(V).then(function (c) { c.put(req, cl); }); }
    return r;
  }).catch(function () { return caches.match(req).then(function (hit) { return hit || caches.match("index.html"); }); });
}
self.addEventListener("fetch", function (e) {
  var req = e.request; if (req.method !== "GET") return;
  var u = new URL(req.url);
  if (u.origin === location.origin) {
    if (req.mode === "navigate" || /\/(index\.html)?$/.test(u.pathname)) { e.respondWith(networkFirst(req)); return; }
    if (/\.pmtiles$/.test(u.pathname)) return;
    if (/\/duchove\/|\.(png|webmanifest)$/.test(u.pathname)) { e.respondWith(cacheFirst(req)); return; }
    return;
  }
  if (/fonts\.(googleapis|gstatic)\.com$/.test(u.hostname)) e.respondWith(cacheFirst(req));
});
