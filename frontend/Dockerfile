# Stage 1: Build the Vite + React frontend
FROM node:20-alpine AS builder

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .

# Build production assets (Vite outputs to dist/)
RUN npm run build

# Stage 2: Serve compiled assets with Nginx reverse proxy
FROM nginx:1.27-alpine AS runner

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=15s --timeout=3s --retries=3 \
    CMD wget -qO- http://localhost:80/nginx-health || exit 1

CMD ["nginx", "-g", "daemon off;"]
