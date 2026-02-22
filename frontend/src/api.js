// frontend/src/api.js
import axios from 'axios';

const BASE = process.env.REACT_APP_API_URL || 'http://localhost:8000';
const WS   = process.env.REACT_APP_WS_URL  || 'ws://localhost:8000/ws';

// Axios instance with sensible defaults
const http = axios.create({
  baseURL: BASE,
  timeout: 30_000,
  headers: { 'Content-Type': 'application/json' },
});

// ── REST helpers ──────────────────────────────────────────────────────────────

export const api = {
  createDownload: (url)  => http.post('/downloads', { url }).then(r => r.data),
  getDownloads:   ()     => http.get('/downloads').then(r => r.data),
  cancelDownload: (id)   => http.delete(`/downloads/${id}`),
  getCookiesInfo: ()     => http.get('/cookies').then(r => r.data),
  saveCookies:    (text) => http.post('/cookies', { cookies_content: text }).then(r => r.data),
  deleteCookies:  ()     => http.delete('/cookies').then(r => r.data),
};

// ── WebSocket service ─────────────────────────────────────────────────────────

export class WSService {
  constructor() {
    this._url       = WS;
    this._ws        = null;
    this._handlers  = {};       // { eventType: [fn, ...] }
    this._ping      = null;
    this._retries   = 0;
    this._maxRetry  = 8;
    this._closed    = false;    // set true on intentional disconnect
  }

  connect() {
    return new Promise((resolve, reject) => {
      try {
        this._ws = new WebSocket(this._url);

        this._ws.onopen = () => {
          this._retries = 0;
          this._startPing();
          resolve();
        };

        this._ws.onmessage = ({ data }) => {
          try {
            const { type, data: payload } = JSON.parse(data);
            (this._handlers[type] || []).forEach(fn => fn(payload));
          } catch { /* ignore malformed frames */ }
        };

        this._ws.onerror = () => {
          reject(new Error('WebSocket connection error'));
        };

        this._ws.onclose = () => {
          this._stopPing();
          if (!this._closed) this._scheduleReconnect();
        };
      } catch (err) {
        reject(err);
      }
    });
  }

  on(type, fn) {
    (this._handlers[type] = this._handlers[type] || []).push(fn);
    return () => this.off(type, fn); // returns unsubscribe fn
  }

  off(type, fn) {
    if (this._handlers[type])
      this._handlers[type] = this._handlers[type].filter(f => f !== fn);
  }

  disconnect() {
    this._closed = true;
    this._stopPing();
    if (this._ws) { this._ws.close(); this._ws = null; }
  }

  _startPing() {
    this._stopPing();
    // Heartbeat every 25 s to prevent proxy timeouts
    this._ping = setInterval(() => {
      if (this._ws?.readyState === WebSocket.OPEN) this._ws.send('ping');
    }, 25_000);
  }

  _stopPing() {
    if (this._ping) { clearInterval(this._ping); this._ping = null; }
  }

  _scheduleReconnect() {
    if (this._retries >= this._maxRetry) {
      console.warn('WS: max reconnection attempts reached');
      return;
    }
    // Exponential back-off: 1s, 2s, 4s … capped at 30s
    const delay = Math.min(1000 * 2 ** this._retries, 30_000);
    this._retries++;
    console.log(`WS: reconnecting in ${delay}ms (attempt ${this._retries})`);
    setTimeout(() => {
      this.connect().catch(() => { /* next onclose will schedule again */ });
    }, delay);
  }
}