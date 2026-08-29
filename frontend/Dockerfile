# Static build served by nginx. Used for `gcloud run deploy --source .` if you
# host the dashboard on Cloud Run rather than Firebase Hosting.
#
# VITE_API_BASE is baked in at build time (Vite inlines import.meta.env), so it
# must be passed as a build arg, not a runtime env var:
#   gcloud run deploy followthrough-frontend --source . \
#     --build-arg VITE_API_BASE=https://your-backend-url

FROM node:22-alpine AS build
WORKDIR /app

COPY package*.json ./
RUN npm ci

ARG VITE_API_BASE=http://localhost:8080
ENV VITE_API_BASE=$VITE_API_BASE

COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html

# Cloud Run supplies $PORT (8080); nginx defaults to 80, so rewrite the port
# and serve index.html for unknown paths (single-page app).
RUN printf 'server {\n\
  listen 8080;\n\
  root /usr/share/nginx/html;\n\
  index index.html;\n\
  location / { try_files $uri $uri/ /index.html; }\n\
}\n' > /etc/nginx/conf.d/default.conf

EXPOSE 8080
CMD ["nginx", "-g", "daemon off;"]
