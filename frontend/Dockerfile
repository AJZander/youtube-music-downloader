# frontend/Dockerfile
FROM node:18-alpine AS build

WORKDIR /app

# Accept build-time API URLs so React can bake them into the bundle
ARG REACT_APP_API_URL=http://localhost:8000
ARG REACT_APP_WS_URL=ws://localhost:8000/ws
ENV REACT_APP_API_URL=$REACT_APP_API_URL
ENV REACT_APP_WS_URL=$REACT_APP_WS_URL

COPY package*.json ./
# npm install generates a lockfile on first run; npm ci requires one to exist.
# We use --legacy-peer-deps to avoid peer-dep conflicts in react-scripts 5.
RUN npm install --legacy-peer-deps

COPY public ./public
COPY src ./src
RUN npm run build

# ── Production nginx stage ────────────────────────────────────────────────────
FROM nginx:1.25-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/build /usr/share/nginx/html

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]