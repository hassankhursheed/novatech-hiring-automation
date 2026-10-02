# syntax=docker/dockerfile:1.7
# ---------------------------------------------------------------------------------------------
# Build: type-check and bundle the portal. API locations are baked in at build time.
# ---------------------------------------------------------------------------------------------
FROM node:24-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
COPY . .
ARG VITE_API_URL=http://localhost:8000
ARG VITE_INTAKE_URL=http://localhost:5678/webhook/applications
ENV VITE_API_URL=$VITE_API_URL VITE_INTAKE_URL=$VITE_INTAKE_URL
RUN npm run build

# ---------------------------------------------------------------------------------------------
# Runtime: static files on unprivileged nginx (non-root, port 8080) with SPA fallback and
# security headers. connect-src of the Content-Security-Policy is filled in at start-up.
# ---------------------------------------------------------------------------------------------
FROM nginxinc/nginx-unprivileged:1.29-alpine AS runtime
LABEL org.opencontainers.image.title="novatech-hiring-portal"
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html
ENV PORTAL_CONNECT_SRC="http://localhost:8000 http://localhost:5678"
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD wget -qO- http://127.0.0.1:8080/healthz || exit 1
