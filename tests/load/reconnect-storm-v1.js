import exec from 'k6/execution';
import { Counter, Rate } from 'k6/metrics';
import { WebSocket } from 'k6/websockets';

const WS_URL = __ENV.LIQI_WS_URL || '';
const RELEASE_ID = __ENV.LIQI_RELEASE_ID || '';
const AUTH_TOKEN = __ENV.LIQI_AUTH_TOKEN || '';
const INITIAL_BODY = __ENV.LIQI_WS_INITIAL_JSON || '';
const RESUME_BODY = __ENV.LIQI_WS_RESUME_JSON || '';
const SUCCESS_FIELD = __ENV.LIQI_RESUME_SUCCESS_FIELD || '';
const SUCCESS_VALUE = __ENV.LIQI_RESUME_SUCCESS_VALUE || 'true';
const DURABLE_LOSS_FIELD = __ENV.LIQI_DURABLE_LOSS_FIELD || '';
const WARMUP_MS = Number(__ENV.LIQI_RECONNECT_WARMUP_MS || '300000');
const RECONNECT_WINDOW_MS = Number(__ENV.LIQI_RECONNECT_WINDOW_MS || '60000');
const POST_RECONNECT_MS = Number(__ENV.LIQI_POST_RECONNECT_MS || '600000');

const reconnectAttempts = new Counter('liqi_reconnect_attempts');
const reconnectSuccess = new Rate('liqi_reconnect_success');
const durableEventLoss = new Counter('liqi_durable_event_loss');
const connectionErrors = new Rate('liqi_reconnect_connection_errors');

export const options = {
  scenarios: {
    static_sessions: {
      executor: 'ramping-vus',
      exec: 'staticSession',
      startVUs: 0,
      stages: [
        { duration: '5m', target: 1500 },
        { duration: '11m', target: 1500 },
        { duration: '1m', target: 0 },
      ],
    },
    reconnecting_sessions: {
      executor: 'ramping-vus',
      exec: 'reconnectingSession',
      startVUs: 0,
      stages: [
        { duration: '5m', target: 500 },
        { duration: '11m', target: 500 },
        { duration: '1m', target: 0 },
      ],
    },
  },
  thresholds: {
    liqi_reconnect_success: ['rate>0.99'],
    liqi_durable_event_loss: ['count==0'],
    liqi_reconnect_connection_errors: ['rate<0.01'],
  },
};

function required(name, value) {
  if (!value) throw new Error(`${name} is required; resume semantics remain provider-owned`);
}

function render(template) {
  return template
    .replaceAll('{{VU_ID}}', String(exec.vu.idInTest))
    .replaceAll('{{ITERATION}}', String(exec.scenario.iterationInTest))
    .replaceAll('{{RELEASE_ID}}', RELEASE_ID);
}

function headers() {
  const value = { 'X-LIQI-Release-ID': RELEASE_ID, 'X-LIQI-Load-Run': __ENV.LIQI_LOAD_RUN_ID || `reconnect-${RELEASE_ID}` };
  if (AUTH_TOKEN) value.Authorization = `Bearer ${AUTH_TOKEN}`;
  return value;
}

export function setup() {
  required('LIQI_WS_URL', WS_URL);
  required('LIQI_RELEASE_ID', RELEASE_ID);
  required('LIQI_WS_INITIAL_JSON', INITIAL_BODY);
  required('LIQI_WS_RESUME_JSON', RESUME_BODY);
  required('LIQI_RESUME_SUCCESS_FIELD', SUCCESS_FIELD);
  return { releaseId: RELEASE_ID };
}

function inspectMessage(event, resumed) {
  let payload;
  try { payload = JSON.parse(event.data); } catch (_) { return; }
  if (DURABLE_LOSS_FIELD && Number(payload[DURABLE_LOSS_FIELD] || 0) > 0) durableEventLoss.add(Number(payload[DURABLE_LOSS_FIELD]));
  if (resumed && String(payload[SUCCESS_FIELD]) === SUCCESS_VALUE) reconnectSuccess.add(true);
}

export function staticSession() {
  const socket = new WebSocket(WS_URL, [], { headers: headers() });
  let opened = false;
  socket.addEventListener('open', () => { opened = true; socket.send(render(INITIAL_BODY)); });
  socket.addEventListener('message', (event) => inspectMessage(event, false));
  socket.addEventListener('error', () => connectionErrors.add(true));
  socket.addEventListener('close', () => connectionErrors.add(!opened));
  setTimeout(() => socket.close(1000, 'storm-complete'), WARMUP_MS + RECONNECT_WINDOW_MS + POST_RECONNECT_MS);
}

export function reconnectingSession() {
  const first = new WebSocket(WS_URL, [], { headers: headers() });
  let firstOpened = false;
  first.addEventListener('open', () => { firstOpened = true; first.send(render(INITIAL_BODY)); });
  first.addEventListener('message', (event) => inspectMessage(event, false));
  first.addEventListener('error', () => connectionErrors.add(true));
  first.addEventListener('close', () => connectionErrors.add(!firstOpened));

  const jitter = Math.floor(Math.random() * RECONNECT_WINDOW_MS);
  setTimeout(() => {
    first.close(1012, 'reconnect-storm');
    setTimeout(() => {
      reconnectAttempts.add(1);
      const second = new WebSocket(WS_URL, [], { headers: headers() });
      let secondOpened = false;
      let successObserved = false;
      second.addEventListener('open', () => { secondOpened = true; second.send(render(RESUME_BODY)); });
      second.addEventListener('message', (event) => {
        let payload;
        try { payload = JSON.parse(event.data); } catch (_) { return; }
        if (String(payload[SUCCESS_FIELD]) === SUCCESS_VALUE && !successObserved) {
          successObserved = true;
          reconnectSuccess.add(true);
        }
        if (DURABLE_LOSS_FIELD && Number(payload[DURABLE_LOSS_FIELD] || 0) > 0) durableEventLoss.add(Number(payload[DURABLE_LOSS_FIELD]));
      });
      second.addEventListener('error', () => connectionErrors.add(true));
      second.addEventListener('close', () => {
        connectionErrors.add(!secondOpened);
        if (!successObserved) reconnectSuccess.add(false);
      });
      setTimeout(() => second.close(1000, 'reconnect-observation-complete'), POST_RECONNECT_MS);
    }, jitter);
  }, WARMUP_MS);
}

export function handleSummary(data) {
  const path = __ENV.LIQI_K6_SUMMARY || 'summary-reconnect-storm-v1.json';
  return { [path]: JSON.stringify(data, null, 2), stdout: `k6 reconnect summary written to ${path}\n` };
}
