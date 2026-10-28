import {defineConfig} from 'vitest/config';
import react from '@vitejs/plugin-react';

const backend = 'http://127.0.0.1:8877';

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1', port: 5173, strictPort: true, cors: false,
    proxy: {'/api': {
      target: backend, changeOrigin: true,
      configure(proxy) {
        // Only the loopback development proxy rewrites its own upstream origin.
        proxy.on('proxyReq', request => request.setHeader('Origin', backend));
      },
    }},
  },
  build: {target: ['safari15', 'edge109'], cssTarget: 'safari15'},
  test: {environment: 'jsdom', setupFiles: ['./src/test/setup.ts'], clearMocks: true},
});
