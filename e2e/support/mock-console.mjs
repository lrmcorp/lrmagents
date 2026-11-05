// Stands in for the console when only the gateway's pages are tested: always signed in, two agents.
import http from 'node:http';

http.createServer((req, res) => {
  res.setHeader('Content-Type', 'application/json');
  if (req.url.startsWith('/auth/check')) return res.end(JSON.stringify({ authenticated: true }));
  if (req.url.startsWith('/api/agents')) return res.end(JSON.stringify({ agents: [{ id: 'default', name: 'Lorium' }, { id: 'lrm-cfo', name: 'CFO' }] }));
  res.statusCode = 404;
  res.end('{}');
}).listen(Number(process.env.PORT || 9998), '127.0.0.1');
