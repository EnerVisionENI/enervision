# Build stage
# Node 20 est EOL (avril 2026) et ne satisfait plus les engines de certaines
# deps transitives (js-beautify -> nopt -> abbrev, qui exigent >=22.22 /
# >=24.15 / >=26) : aligné sur le node-version de ci-cd.yml (build-front).
FROM node:24-alpine AS builder

WORKDIR /app

# Copy package files from the frontend app
COPY front/package*.json ./

# Install dependencies
RUN npm ci

# Copy source code
COPY front/. .

# Build the Vue app
RUN npm run build

# Production stage - Nginx
FROM nginx:alpine

# OS_PATCH_DATE est référencé dans le RUN ci-dessous, et non seulement déclaré : BuildKit
# n'invalide le cache qu'à la PREMIÈRE UTILISATION d'un ARG, pas à sa définition. La CI y
# injecte la date du jour, ce qui force le patch à rejouer une fois par jour.
#
# Sans ce garde-fou, `cache-from` resservait indéfiniment la couche de patch d'un build
# antérieur : l'image repartait avec les paquets figés à cette date-là, alors que le correctif
# était disponible en dépôt, et Trivy échouait sur des CVE que ce RUN corrige pourtant.
ARG OS_PATCH_DATE=unknown
RUN echo "patch OS : $OS_PATCH_DATE" \
    && apk update && apk upgrade --no-cache

# Copy nginx config
COPY infra/nginx.conf /etc/nginx/conf.d/default.conf

# Copy built files from builder
COPY --from=builder /app/dist /usr/share/nginx/html

# Expose port
EXPOSE 80

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://localhost/health || exit 1

# Start nginx
CMD ["nginx", "-g", "daemon off;"]
