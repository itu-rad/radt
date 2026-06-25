const express = require('express');
const { query, parseInList } = require('./db');
require('dotenv').config();

// --- preload query files at startup so we don't read them per-request ---
const fs = require('fs');
const path = require('path');
const queriesDir = path.join(__dirname, 'queries');
const loadFile = (name) => {
  try {
    return fs.readFileSync(path.join(queriesDir, name), 'utf8');
  } catch (err) {
    console.warn(`Query file not found: ${name} (looking in ${queriesDir})`);
    return null;
  }
};
const queries = {
  fe_experiments: loadFile('fe_experiments.sql'),
  fe_runs: loadFile('fe_runs.sql'),
  fe_metrics_available: loadFile('fe_metrics_available.sql'),
  fe_metrics: loadFile('fe_metrics.sql'),
};

// validate required queries are present at startup (fail fast)
const required = [
  'fe_experiments',
  'fe_runs',
  'fe_metrics_available',
  'fe_metrics'
];
for (const key of required) {
  if (!queries[key]) {
    console.error(`Missing required query file: ${key}.sql in ${queriesDir}`);
    process.exit(1);
  }
}

const app = express();
const port = process.env.PORT || 4000;

app.use(express.json());

// Add CORS headers and handle preflight requests
app.use((req, res, next) => {
  const origin = process.env.CORS_ORIGIN || '*';
  res.header('Access-Control-Allow-Origin', origin);
  res.header('Access-Control-Allow-Methods', 'GET,POST,PUT,DELETE,OPTIONS');
  res.header('Access-Control-Allow-Headers', 'Origin, X-Requested-With, Content-Type, Accept, Authorization');
  if (process.env.CORS_ALLOW_CREDENTIALS === 'true') {
    res.header('Access-Control-Allow-Credentials', 'true');
  }
  if (req.method === 'OPTIONS') {
    return res.sendStatus(204);
  }
  next();
});

const applyReplacements = (template, replacements = {}) => {
  let sql = template;
  for (const [key, value] of Object.entries(replacements)) {
    const token = new RegExp(`\\$${key}\\$`, 'g');
    sql = sql.replace(token, value);
  }
  return sql;
};

async function executeQuery(queryKey, { replacements = {}, params = [] } = {}) {
  const template = queries[queryKey];
  if (!template) throw new Error(`Query template missing: ${queryKey}`);
  const sql = applyReplacements(template, replacements);
  console.log('SQL:', sql, 'Params:', params);
  return await query(sql, params.length ? params : undefined);
}

// Simple health check
app.get('/health', (_req, res) => res.json({ ok: true }));

// fe_experiments -> expects to return rows with experiment_id and name
app.get('/fe_experiments', async (req, res) => {
  try {
    const rows = await executeQuery('fe_experiments');
    res.json(rows);
  } catch (err) {
    console.error(err);
    res.status(500).json({ error: 'internal' });
  }
});

// fe_runs -> return run rows
app.get('/fe_runs', async (req, res) => {
  try {
    const experiment_id = req.query.experiment_id;
    const rows = experiment_id
      ? await executeQuery('fe_runs', { params: [experiment_id] })
      : await executeQuery('fe_runs');
    res.json(rows);
  } catch (err) {
    console.error(err);
    res.status(500).json({ error: 'internal' });
  }
});

// fe_metrics_available -> expects to accept ?run_uuid=in.(...)
app.get('/fe_metrics_available', async (req, res) => {
  try {
    const runParam = req.query.run_uuid;
    const runs = parseInList(runParam);
    if (runs.length === 0) {
      return res.json([]);
    }
    const placeholders = runs.map((_, i) => '$' + (i + 1)).join(',');
    const rows = await executeQuery('fe_metrics_available', {
      replacements: { IN_RUNS: placeholders },
      params: runs
    });
    res.json(rows);
  } catch (err) {
    console.error(err);
    res.status(500).json({ error: 'internal' });
  }
});

// fe_metrics -> expects ?run_uuid=in(...)&key=eq.METRIC
app.get('/fe_metrics', async (req, res) => {
  try {
    const runParam = req.query.run_uuid;
    const keyParam = req.query.key;
    const runs = parseInList(runParam);
    if (runs.length === 0) return res.json([]);

    const m = keyParam.match(/eq\.(.*)/);
    const metric = decodeURIComponent(m[1]);

    const runPlaceholders = runs.map((_, i) => '$' + (i + 1)).join(',');
    const sqlReplacements = {
      IN_RUNS: runPlaceholders,
      EQ_METRIC: '$' + (runs.length + 1)
    };
    const params = [...runs, metric];

    const rows = await executeQuery('fe_metrics', {
      replacements: sqlReplacements,
      params
    });
    res.json(rows);
  } catch (err) {
    console.error(err);
    res.status(500).json({ error: 'internal' });
  }
});

app.listen(port, () => {
  console.log(`API server listening on http://localhost:${port}`);
});
