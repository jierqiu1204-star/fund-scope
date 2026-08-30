FROM node:22-alpine AS builder

WORKDIR /app

# Keep static builds inside the 2-core/4-GB VPS memory envelope. A bounded
# Node heap fails the build cleanly instead of letting the host OOM killer
# terminate the deployment runner and production services.
ENV NODE_OPTIONS=--max-old-space-size=1536

COPY package.json pnpm-lock.yaml ./
RUN corepack enable \
  && corepack pnpm config set registry https://registry.npmmirror.com \
  && corepack pnpm install --frozen-lockfile

COPY . .
ARG NEXT_PUBLIC_API_BASE_URL=
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL
RUN corepack pnpm build:static

CMD ["sh", "-c", "cp -R /app/out /exported"]
