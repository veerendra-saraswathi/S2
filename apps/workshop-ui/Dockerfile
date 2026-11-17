# S2 Workshop UI. Build context: the repository root.
#
#   docker build -f apps/workshop-ui/Dockerfile -t s2/workshop-ui .
#
# Serves the production build with `vite preview`, which proxies /api to the ontology
# engine (S2_API_URL), so the browser talks to one origin, as in development.

FROM node:22-slim

RUN npm install --global pnpm@10.28.0

WORKDIR /app
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/workshop-ui/package.json apps/workshop-ui/package.json
RUN pnpm install --frozen-lockfile --filter workshop-ui...

COPY apps/workshop-ui apps/workshop-ui
# Sign-in and map settings are read at build time (VITE_*); the defaults suit the local stack.
ARG VITE_OIDC_AUTHORITY=http://localhost:8080/realms/s2
ARG VITE_OIDC_CLIENT_ID=s2-workshop
ENV VITE_OIDC_AUTHORITY=$VITE_OIDC_AUTHORITY VITE_OIDC_CLIENT_ID=$VITE_OIDC_CLIENT_ID
RUN pnpm --filter workshop-ui build && chown -R node /app/apps/workshop-ui

USER node
WORKDIR /app/apps/workshop-ui
EXPOSE 5173
CMD ["pnpm", "exec", "vite", "preview", "--host", "0.0.0.0", "--port", "5173", "--strictPort"]
