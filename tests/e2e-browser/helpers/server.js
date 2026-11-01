// Boots a throwaway ShipCheck server for browser specs and tears it down.
// Degraded mode by default (MODEL_API_KEY=""), so no model key or credits
// are needed. One server per run.js invocation; specs share ctx.base.
'use strict';
const { spawn } = require('child_process');
const fs = require('fs');
const net = require('net');
const os = require('os');
const path = require('path');

const ROOT = path.resolve(__dirname, '..', '..', '..');

function portFree(port) {
  return new Promise(resolve => {
    const s = net.connect(port, '127.0.0.1');
    s.on('connect', () => { s.destroy(); resolve(false); });
    s.on('error', () => resolve(true));
  });
}

async function findPort() {
  for (let p = 8781; p <= 8790; p++) {
    if (await portFree(p)) return p; // eslint-disable-line no-await-in-loop
  }
  throw new Error('no free port in 8781..8790');
}

function waitReady(base, timeoutMs) {
  const t0 = Date.now();
  return new Promise((resolve, reject) => {
    const tick = () => {
      fetch(base + '/api/status').then(r => {
        if (r.ok) return resolve();
        throw new Error('status ' + r.status);
      }).catch(() => {
        if (Date.now() - t0 > timeoutMs) return reject(new Error('server never ready at ' + base));
        setTimeout(tick, 300);
      });
    };
    tick();
  });
}

async function startServer(opts = {}) {
  const venvPy = path.join(ROOT, '.venv', 'bin', 'python');
  if (!fs.existsSync(venvPy)) {
    throw new Error('missing ' + venvPy + ' — create the project venv first (see README)');
  }
  const port = await findPort();
  const db = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'shipcheck-e2e-')), 'e2e.db');
  const args = ['-m', 'uvicorn', 'atlas.api.app:app', '--host', '127.0.0.1', '--port', String(port)];
  const env = Object.assign({}, process.env, {
    PYTHONPATH: path.join(ROOT, 'src'),
    ATLAS_DB: db,
    MODEL_API_KEY: opts.live ? (process.env.MODEL_API_KEY || '') : '',
  });
  const child = spawn(venvPy, args, { cwd: ROOT, env, stdio: ['ignore', 'pipe', 'pipe'] });
  let logged = '';
  child.stderr.on('data', d => { logged += d.toString().slice(-2000); });
  const base = 'http://127.0.0.1:' + port;
  try {
    await waitReady(base, 30000);
  } catch (e) {
    child.kill();
    throw new Error(e.message + ' | server stderr tail: ' + logged.slice(-500));
  }
  return {
    base, port, db,
    mode: opts.live ? 'live' : 'degraded',
    stop: () => new Promise(resolve => {
      child.on('exit', () => resolve());
      child.kill();
      setTimeout(resolve, 3000).unref?.();
    }),
  };
}

module.exports = { startServer, ROOT };
