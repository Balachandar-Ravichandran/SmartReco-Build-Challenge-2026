/**
 * The only JavaScript in the app that talks to the backend.
 * Batches behavioral_events client-side and POSTs to /api/v1/events.
 * Does NOT fetch or render recommendation data (Section 0.1, 5.4).
 */
(function () {
  const FLUSH_INTERVAL_MS = 5000;
  const FLUSH_MAX_EVENTS = 20;

  let queue = [];
  let flushTimer = null;

  function scheduleFlush() {
    if (flushTimer) return;
    flushTimer = setTimeout(flush, FLUSH_INTERVAL_MS);
  }

  function flush(useBeacon) {
    if (flushTimer) {
      clearTimeout(flushTimer);
      flushTimer = null;
    }
    if (queue.length === 0) return;

    const batch = queue.splice(0, queue.length);
    const userId = window.PATHWISE_USER_ID || "u_demo";
    const url = `/api/v1/events?user_id=${encodeURIComponent(userId)}`;

    if (useBeacon && navigator.sendBeacon) {
      navigator.sendBeacon(url, new Blob([JSON.stringify(batch)], { type: "application/json" }));
      return;
    }

    fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(batch),
    }).catch(() => {
      // Best-effort — dropped events are acceptable for a client-side tracker.
    });
  }

  window.pathwiseTrack = function (event, immediate) {
    queue.push(event);
    if (immediate || queue.length >= FLUSH_MAX_EVENTS) {
      flush(!!immediate);
    } else {
      scheduleFlush();
    }
  };

  window.addEventListener("beforeunload", () => flush(true));
})();
