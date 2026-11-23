// Stands in for the console when only the gateway's pages are tested: signed in (unless the
// request carries the cookie e2e_signed_out=1, for the signed-out tests), two agents.
import http from 'node:http';

http.createServer((req, res) => {
  res.setHeader('Content-Type', 'application/json');
  if (req.url.startsWith('/auth/check')) return res.end(JSON.stringify({ authenticated: !/(^|;\s*)e2e_signed_out=1/.test(req.headers.cookie || '') }));
  if (req.url.startsWith('/api/agents')) return res.end(JSON.stringify({ agents: [{ id: 'default', name: 'Lorium' }, { id: 'lrm-cfo', name: 'CFO' }] }));
  res.statusCode = 404;
  res.end('{}');
}).listen(Number(process.env.PORT || 9998), '127.0.0.1');
