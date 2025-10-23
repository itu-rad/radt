const express = require('express');
const { query, parseInList } = require('./db');
require('dotenv').config();

const app = express();
const port = process.env.PORT || 4000;

app.use(express.json());

// Simple health check
app.get('/health', (_req, res) => res.json({ ok: true }));

// fe_experiments -> expects to return rows with experiment_id and name
app.get('/fe_experiments', async (req, res) => {
  try {
    // If a query param is provided, you may handle filtering here. For now return all.
    const rows = await query(process.env.QUERY_EXPERIMENTS || 'SELECT experiment_id, name FROM fe_experiments LIMIT 100');
    res.json(rows);
  } catch (err) {
    console.error(err);
    res.status(500).json({ error: 'internal' });
  }
});

// fe_runs -> return run rows
app.get('/fe_runs', async (req, res) => {
  try {
    // Allow optional filtering by experiment_id via ?experiment_id=123
    const experiment_id = req.query.experiment_id;
    let rows;
    if (experiment_id) {
      rows = await query(process.env.QUERY_RUNS_BY_EXPERIMENT || 'SELECT * FROM fe_runs WHERE experiment_id = $1', [experiment_id]);
    } else {
      rows = await query(process.env.QUERY_RUNS || 'SELECT * FROM fe_runs LIMIT 500');
    }
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
    // Query unique metric keys for given runs
    const placeholders = runs.map((_, i) => '$' + (i + 1)).join(',');
    const sql = process.env.QUERY_METRICS_AVAILABLE || `SELECT DISTINCT key FROM fe_metrics_available WHERE run_uuid IN (${placeholders})`;
    const rows = await query(sql, runs);
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
    const keyParam = req.query.key; // expected eq.metric
    const runs = parseInList(runParam);
    if (runs.length === 0) return res.json([]);
    let metric = null;
    if (keyParam) {
      const m = keyParam.match(/eq\.(.*)/);
      if (m) metric = decodeURIComponent(m[1]);
    }
    const runPlaceholders = runs.map((_, i) => '$' + (i + 1)).join(',');
    let sql;
    let params = [...runs];
    if (metric !== null) {
      // add metric as last param
      sql = process.env.QUERY_METRICS_BY_RUN_AND_KEY || `SELECT run_uuid, key, step, timestamp, value FROM fe_metrics WHERE run_uuid IN (${runPlaceholders}) AND key = $${runs.length + 1} ORDER BY run_uuid, step`;
      params.push(metric);
    } else {
      sql = process.env.QUERY_METRICS_BY_RUN || `SELECT run_uuid, key, step, timestamp, value FROM fe_metrics WHERE run_uuid IN (${runPlaceholders}) ORDER BY run_uuid, step`;
    }
    const rows = await query(sql, params.length ? params : undefined);
    res.json(rows);
  } catch (err) {
    console.error(err);
    res.status(500).json({ error: 'internal' });
  }
});

app.listen(port, () => {
  console.log(`API server listening on http://localhost:${port}`);
});
