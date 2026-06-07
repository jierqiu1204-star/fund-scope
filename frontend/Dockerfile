FROM node:22-alpine AS builder

WORKDIR /app

COPY package.json pnpm-lock.yaml ./
RUN corepack enable \
  && corepack pnpm config set registry https://registry.npmmirror.com \
  && corepack pnpm install --frozen-lockfile

COPY . .
ARG NEXT_PUBLIC_API_BASE_URL=http://110.42.222.9
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL
RUN corepack pnpm build:static

CMD ["sh", "-c", "cp -R /app/out /exported"]
