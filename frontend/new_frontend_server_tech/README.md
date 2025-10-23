# PG Static Server

Simple TypeScript server that runs a static query against a Postgres database server-side and returns JSON at /data.

Setup

1. Copy `.env.example` to `.env` and set `DATABASE_URL`.
2. npm install
3. npm run build
4. npm start

Example:

curl http://localhost:3000/data

Docker

You can build and run with Docker if you don't have Node installed locally:

docker build -t pg-static-server .
docker run -e DATABASE_URL="postgresql://user:pass@host:5432/db" -p 3000:3000 pg-static-server

