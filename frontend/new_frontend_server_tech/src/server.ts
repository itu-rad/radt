import express from 'express';
import { queryStatic, closePool } from './db';

const app = express();
const port = Number(process.env.PORT || 3000);

app.get('/data', async (_req, res) => {
    try {
        const rows = await queryStatic();
        res.json({ ok: true, rows });
    } catch (err) {
        console.error('Query error', err);
        res.status(500).json({ ok: false, error: 'internal' });
    }
});

const server = app.listen(port, () => {
    console.log(`Server listening on http://localhost:${port}`);
});

process.on('SIGINT', async () => {
    console.log('Shutting down...');
    server.close();
    await closePool();
    process.exit(0);
});
