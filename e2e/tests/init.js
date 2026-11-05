// Collect page errors so every test can fail on them (read with window.__lrmErrors).
window.__lrmErrors = [];
addEventListener('error', (e) => window.__lrmErrors.push(String(e.message || e)));
addEventListener('unhandledrejection', (e) => window.__lrmErrors.push(String(e.reason?.message || e.reason)));
