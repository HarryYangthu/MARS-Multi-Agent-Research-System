import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const backend = process.env.MARS_APP_BACKEND ?? 'http://127.0.0.1:8020';

export default defineConfig({
  plugins: [react()],
  server: { host: '127.0.0.1', port: 5180, proxy: { '/api': backend } },
});
