FROM node:22-alpine AS builder

WORKDIR /app

COPY package.json pnpm-lock.yaml ./
RUN corepack enable && corepack pnpm install --frozen-lockfile

COPY . .
RUN corepack pnpm build:static

CMD ["sh", "-c", "cp -R /app/out /exported"]
