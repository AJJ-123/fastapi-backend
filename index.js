// RacingPro Sync API — deploy to Railway
// Every device hits this API. Data lives in Railway Postgres.
// Auth: single shared passcode (hashed with bcrypt)

const express = require('express');
const cors    = require('cors');
const { Pool } = require('pg');
const bcrypt  = require('bcryptjs');

const app  = express();
const port = process.env.PORT || 3000;

// ── Database ──────────────────────────────────────────────────
const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  ssl: { rejectUnauthorized: false }
});

// ── Middleware ────────────────────────────────────────────────
app.use(cors({ origin: '*' }));
app.use(express.json({ limit: '50mb' }));

// ── Bootstrap tables on first start ──────────────────────────
async function bootstrap() {
  await pool.query(`
    CREATE TABLE IF NOT EXISTS auth (
      id      SERIAL PRIMARY KEY,
      code_hash TEXT NOT NULL,
      created_at TIMESTAMPTZ DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS sync_data (
      key       TEXT PRIMARY KEY,
      value     JSONB NOT NULL,
      updated_at TIMESTAMPTZ DEFAULT NOW()
    );
  `);
  console.log('✅ Tables ready');
}

// ── Simple token: just a signed timestamp so we know the
//    client passed the passcode check recently. Not a full
//    JWT — keeps the server tiny.
const TOKEN_SECRET = process.env.TOKEN_SECRET || 'rp-secret-change-me';
function makeToken() {
  const ts = Date.now();
  const sig = Buffer.from(ts + TOKEN_SECRET).toString('base64').slice(0, 16);
  return `${ts}.${sig}`;
}
function validateToken(tok) {
  if (!tok) return false;
  const [ts, sig] = (tok || '').split('.');
  if (!ts || !sig) return false;
  const expected = Buffer.from(ts + TOKEN_SECRET).toString('base64').slice(0, 16);
  if (sig !== expected) return false;
  // Token valid for 90 days
  return (Date.now() - parseInt(ts)) < 90 * 24 * 60 * 60 * 1000;
}

// ── Auth middleware ───────────────────────────────────────────
function requireAuth(req, res, next) {
  const tok = req.headers['x-rp-token'];
  if (!validateToken(tok)) return res.status(401).json({ error: 'Unauthorised' });
  next();
}

// ─────────────────────────────────────────────────────────────
// ROUTES
// ─────────────────────────────────────────────────────────────

// Health check
app.get('/', (req, res) => res.json({ ok: true, service: 'RacingPro API' }));

// ── POST /auth/setup  — set the passcode for the first time ──
app.post('/auth/setup', async (req, res) => {
  try {
    const { code } = req.body;
    if (!code || code.length < 6) return res.status(400).json({ error: 'Code too short' });
    const { rows } = await pool.query('SELECT id FROM auth LIMIT 1');
    if (rows.length > 0) return res.status(409).json({ error: 'Code already set. Use /auth/change to update it.' });
    const hash = await bcrypt.hash(code, 10);
    await pool.query('INSERT INTO auth (code_hash) VALUES ($1)', [hash]);
    const token = makeToken();
    res.json({ ok: true, token });
  } catch (e) {
    console.error(e);
    res.status(500).json({ error: 'Server error' });
  }
});

// ── POST /auth/login  — verify passcode, return token ────────
app.post('/auth/login', async (req, res) => {
  try {
    const { code } = req.body;
    if (!code) return res.status(400).json({ error: 'No code provided' });
    const { rows } = await pool.query('SELECT code_hash FROM auth LIMIT 1');
    if (!rows.length) return res.status(404).json({ error: 'No code set yet. Visit the app and use Set Code first.' });
    const ok = await bcrypt.compare(code, rows[0].code_hash);
    if (!ok) return res.status(401).json({ error: 'Wrong access code' });
    const token = makeToken();
    res.json({ ok: true, token });
  } catch (e) {
    console.error(e);
    res.status(500).json({ error: 'Server error' });
  }
});

// ── GET /auth/check  — check if a code is set ───────────────
app.get('/auth/check', async (req, res) => {
  try {
    const { rows } = await pool.query('SELECT id FROM auth LIMIT 1');
    res.json({ hasCode: rows.length > 0 });
  } catch (e) {
    res.status(500).json({ error: 'Server error' });
  }
});

// ── POST /auth/change  — change passcode ─────────────────────
app.post('/auth/change', async (req, res) => {
  try {
    const { oldCode, newCode } = req.body;
    if (!newCode || newCode.length < 6) return res.status(400).json({ error: 'New code too short' });
    const { rows } = await pool.query('SELECT id, code_hash FROM auth LIMIT 1');
    if (!rows.length) return res.status(404).json({ error: 'No code set' });
    const ok = await bcrypt.compare(oldCode, rows[0].code_hash);
    if (!ok) return res.status(401).json({ error: 'Current code incorrect' });
    const hash = await bcrypt.hash(newCode, 10);
    await pool.query('UPDATE auth SET code_hash=$1 WHERE id=$2', [hash, rows[0].id]);
    const token = makeToken();
    res.json({ ok: true, token });
  } catch (e) {
    res.status(500).json({ error: 'Server error' });
  }
});

// ── GET /sync/:key  — load a data blob ───────────────────────
app.get('/sync/:key', requireAuth, async (req, res) => {
  try {
    const { rows } = await pool.query(
      'SELECT value, updated_at FROM sync_data WHERE key=$1',
      [req.params.key]
    );
    if (!rows.length) return res.json({ value: null });
    res.json({ value: rows[0].value, updated_at: rows[0].updated_at });
  } catch (e) {
    res.status(500).json({ error: 'Server error' });
  }
});

// ── PUT /sync/:key  — save a data blob ───────────────────────
app.put('/sync/:key', requireAuth, async (req, res) => {
  try {
    const { value } = req.body;
    await pool.query(`
      INSERT INTO sync_data (key, value, updated_at)
      VALUES ($1, $2, NOW())
      ON CONFLICT (key) DO UPDATE
        SET value = EXCLUDED.value,
            updated_at = NOW()
    `, [req.params.key, JSON.stringify(value)]);
    res.json({ ok: true });
  } catch (e) {
    res.status(500).json({ error: 'Server error' });
  }
});

// ── DELETE /sync/:key  — delete a data blob ──────────────────
app.delete('/sync/:key', requireAuth, async (req, res) => {
  try {
    await pool.query('DELETE FROM sync_data WHERE key=$1', [req.params.key]);
    res.json({ ok: true });
  } catch (e) {
    res.status(500).json({ error: 'Server error' });
  }
});

// ── GET /sync  — list all keys (useful for debugging) ────────
app.get('/sync', requireAuth, async (req, res) => {
  try {
    const { rows } = await pool.query('SELECT key, updated_at FROM sync_data ORDER BY updated_at DESC');
    res.json({ keys: rows });
  } catch (e) {
    res.status(500).json({ error: 'Server error' });
  }
});

// ─────────────────────────────────────────────────────────────
bootstrap().then(() => {
  app.listen(port, () => console.log(`RacingPro API running on port ${port}`));
}).catch(e => {
  console.error('Bootstrap failed:', e);
  process.exit(1);
});
