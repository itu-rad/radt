const { Pool } = require('pg');
const url = require('url');
require('dotenv').config();

const connectionString = process.env.DATABASE_URL;
if (!connectionString) {
  console.warn('WARNING: DATABASE_URL not set. Server will fail on DB calls.');
}

const pool = new Pool({ connectionString });

async function query(text, params) {
  const client = await pool.connect();
  try {
    const res = await client.query(text, params);
    return res.rows;
  } finally {
    client.release();
  }
}

function parseInList(paramValue) {
  // paramValue expected like: in.("a","b") or in.(a,b)
  if (!paramValue) return [];
  const m = paramValue.match(/in\.\((.*)\)/);
  if (!m) return [];
  return m[1]
    .split(',')
    .map(s => s.trim())
    .map(s => s.replace(/^"|"$/g, ''))
    .filter(s => s.length > 0);
}

module.exports = { pool, query, parseInList };
