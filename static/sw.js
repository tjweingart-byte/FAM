/* FAM's app shell, so the app opens with no connection (27/09 packet).

   Network first, always: online, every request goes to the server exactly as
   it did before this existed, and the copy kept here is only ever what the
   last successful load returned. The cache is used only when the network
   fails. Nothing under /api/ is touched - an episode, a feed, a sign-in all
   stay the server's - and neither is audio: episodes kept for offline
   listening live in the page's own IndexedDB (see `OfflineShelf`).
*/
var SHELL = "fam-shell-v1";
var FILES = ["/", "/index.html", "/fam-audio.js"];

self.addEventListener("install", function (event) {
  event.waitUntil(
    caches.open(SHELL)
      .then(function (cache) { return cache.addAll(FILES); })
      .then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.filter(function (k) { return k !== SHELL; })
                             .map(function (k) { return caches.delete(k); }));
    }).then(function () { return self.clients.claim(); })
  );
});

self.addEventListener("fetch", function (event) {
  var req = event.request;
  if (req.method !== "GET") return;
  var url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  var shell = req.mode === "navigate" && url.pathname === "/"
    ? "/" : (FILES.indexOf(url.pathname) >= 0 ? url.pathname : "");
  if (!shell) return;
  event.respondWith(
    fetch(req).then(function (res) {
      if (res && res.ok) {
        var copy = res.clone();
        caches.open(SHELL).then(function (cache) { cache.put(shell, copy); });
      }
      return res;
    }).catch(function () {
      return caches.match(shell).then(function (hit) {
        return hit || caches.match("/");
      });
    })
  );
});
