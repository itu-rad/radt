import dotenv from 'dotenv';
// @ts-ignore
import { Pool } from 'pg';

dotenv.config();

const pool = new Pool({
    connectionString: process.env.DATABASE_URL,
    // you can also provide host, port, user, password, database via env vars
});

export async function queryStatic(): Promise<any[]> {
    const client = await pool.connect();
    try {
        // Static query - adjust to match your DB
        const res = await client.query(process.env.QUERY);
        return res.rows;
    } finally {
        client.release();
    }
}

export async function closePool() {
    await pool.end();
}
