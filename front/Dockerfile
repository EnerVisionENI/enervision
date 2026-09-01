# Build stage
FROM node:20-alpine AS builder

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

# Patch OS packages regardless of when the base image tag was last rebuilt
RUN apk update && apk upgrade --no-cache

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
