// frontend/src/api.js
import axios from 'axios';

const BASE = process.env.REACT_APP_API_URL || 'http://localhost:8000';
const WS = process.env.REACT_APP_WS_URL || 'ws://localhost:8000/ws';

// Axios instance with sensible defaults
const http = axios.create({
	baseURL: BASE,
	timeout: 60_000,  // Increased timeout for format extraction
	headers: { 'Content-Type': 'application/json' },
});

// ── REST helpers ──────────────────────────────────────────────────────────────

export const api = {
	// Get available formats for a URL
	getFormats: (url) => http.post('/formats', { url }).then(r => r.data),

	// Create download with optional format selection
	createDownload: (url, format_id = null) =>
		http.post('/downloads', { url, format_id }).then(r => r.data),

	// List downloads — all optional: status filter, search term, limit, offset
	getDownloads: ({ status, search, limit = 500, offset = 0 } = {}) => {
		const params = { limit, offset };
		if (status)  params.status = status;
		if (search)  params.search = search;
		return http.get('/downloads', { params }).then(r => r.data);
	},

	// Per-status counts
	getStats: () => http.get('/downloads/stats').then(r => r.data),

	// Cancel a single queued/downloading item
	cancelDownload: (id) => http.delete(`/downloads/${id}`),

	// Retry a failed or cancelled item
	retryDownload: (id) => http.post(`/downloads/${id}/retry`).then(r => r.data),

	// Bulk-delete all downloads with a given status
	bulkDelete: (statusValue) =>
		http.delete('/downloads', { params: { status: statusValue } }),

	// ── Channel Import ──────────────────────────────────────────────────────────
	getChannelPlaylists: (url) =>
		http.post('/channel/playlists', { url }).then(r => r.data),

	queueChannelPlaylists: (playlists) =>
		http.post('/channel/queue-all', { playlists }).then(r => r.data),

	// Get batch processing status
	getBatchStatus: (batchId) =>
		http.get(`/channel/batch/${batchId}`).then(r => r.data),

	// ── Metadata Processing ────────────────────────────────────────────────────
	startMetadataProcessing: (url, options = {}) =>
		http.post('/metadata/process', { url, ...options }).then(r => r.data),

	getMetadataJob: (jobId) =>
		http.get(`/metadata/jobs/${jobId}`).then(r => r.data),

	listMetadataJobs: ({ limit = 50, offset = 0 } = {}) =>
		http.get('/metadata/jobs', { params: { limit, offset } }).then(r => r.data),

	getMetadataJobItems: (jobId, { limit = 1000, offset = 0 } = {}) =>
		http.get(`/metadata/jobs/${jobId}/items`, { params: { limit, offset } }).then(r => r.data),

	cancelMetadataJob: (jobId) =>
		http.post(`/metadata/jobs/${jobId}/cancel`),

	updateMetadataItemSelection: (itemIds, selected) =>
		http.post('/metadata/items/select', { item_ids: itemIds, selected }),

	queueSelectedMetadataItems: (jobId, formatId = 'bestaudio/best') =>
		http.post(`/metadata/jobs/${jobId}/queue-selected`, { format_id: formatId }).then(r => r.data),
};

// ── WebSocket service ─────────────────────────────────────────────────────────

export class WSService {
	constructor() {
		this._url = WS;
		this._ws = null;
		this._handlers = {};       // { eventType: [fn, ...] }
		this._ping = null;
		this._retries = 0;
		this._maxRetry = 8;
		this._closed = false;    // set true on intentional disconnect
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