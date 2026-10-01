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

/* "Your mix is ready" (push.py, the 10.1 packet). The server sends one at a
   mix's listen time, once that day's edition is written. A tap opens the app
   on the mix, focusing a FAM window that is already open rather than a second. */
self.addEventListener("push", function (event) {
  var data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) { data = {}; }
  var title = data.title || "FAM";
  event.waitUntil(self.registration.showNotification(title, {
    body: data.body || "",
    tag: data.tag || "fam",
    data: { url: data.url || "/" }
  }));
});

self.addEventListener("notificationclick", function (event) {
  event.notification.close();
  var url = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(function (list) {
      // Only the app itself (served at "/") understands "open-url"; an open
      // share page, waitlist page or /admin is left alone and a window opens.
      for (var i = 0; i < list.length; i++) {
        var c = list[i], at = new URL(c.url);
        if (at.origin === self.location.origin && at.pathname === "/" && "focus" in c) {
          c.postMessage({ type: "open-url", url: url });
          return c.focus();
        }
      }
      return self.clients.openWindow(url);
    })
  );
});
