FROM node:22-slim AS builder
WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm install

# NEXT_PUBLIC_* variables are inlined into the client bundle at `next build`
# time, not read at container start. Declaring it as a build ARG (and setting
# docker-compose's `build.args`, not `environment:`) is what actually makes it
# take effect -- an `environment:` block on this service would silently do
# nothing, since by the time the container starts the value is already baked
# into the JS that shipped to the browser.
ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL

COPY . .
RUN npm run build

FROM node:22-slim AS runner
WORKDIR /app
ENV NODE_ENV=production

COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static

EXPOSE 3000
CMD ["node", "server.js"]
