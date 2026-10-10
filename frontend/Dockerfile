FROM node:20-alpine

WORKDIR /app/frontend

COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --legacy-peer-deps

COPY frontend/ .
COPY configs/frontend.yaml configs/local_runtime.yaml /app/configs/

ENV FRONTEND_PORT=3000 MARS_HOST=0.0.0.0

EXPOSE 3000
CMD ["npm", "run", "dev"]
