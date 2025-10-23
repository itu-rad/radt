FROM node:20-slim

WORKDIR /app

# Install deps
COPY package.json package-lock.json* ./
RUN npm install --production --no-audit --no-fund

# Build sources
COPY tsconfig.json ./
COPY src ./src
RUN npm install --no-audit --no-fund --save-dev typescript @types/node @types/express && \
    npx tsc

EXPOSE 3000
ENV PORT=3000

CMD ["node", "dist/server.js"]
