import http from 'k6/http';
import exec from 'k6/execution';
import { check } from 'k6';
import { Counter, Rate, Trend } from 'k6/metrics';
import { WebSocket } from 'k6/websockets';

const BASE_URL = __ENV.LIQI_BASE_URL || '';
const WS_URL = __ENV.LIQI_WS_URL || '';
const RELEASE_ID = __ENV.LIQI_RELEASE_ID || '';
const AUTH_TOKEN = __ENV.LIQI_AUTH_TOKEN || '';
const COMMAND_PATH = __ENV.LIQI_COMMAND_PATH || '/v1/readiness/commands';
const EVENT_PATH = __ENV.LIQI_EVENT_PATH || '/v1/readiness/events';
const COMMAND_BODY = __ENV.LIQI_COMMAND_BODY_JSON || '';
const EVENT_BODY = __ENV.LIQI_EVENT_BODY_JSON || '';
const WS_HELLO = __ENV.LIQI_WS_HELLO_JSON || '';
const WS_SUBSCRIBE = __ENV.LIQI_WS_SUBSCRIBE_JSON || '';
const ACTIVE_SUBSCRIPTIONS = Number(__ENV.LIQI_ACTIVE_SUBSCRIPTIONS || '200');
const STEADY_DURATION = __ENV.LIQI_STEADY_DURATION || '30m';
const WS_HOLD_MS = Number(__ENV.LIQI_WS_HOLD_MS || '2100000');

const apiLatency = new Trend('liqi_api_latency_ms', true);
const commandErrors = new Rate('liqi_command_errors');
const eventErrors = new Rate('liqi_event_errors');
const wsConnectErrors = new Rate('liqi_ws_connect_errors');
const wsOpened = new Counter('liqi_ws_opened');
const wsMessages = new Counter('liqi_ws_messages');

export const options = {
  discardResponseBodies: true,
  scenarios: {
    websocket_sessions: {
      executor: 'ramping-vus',
      exec: 'websocketSession',
      startVUs: 0,
      stages: [
        { duration: '5m', target: 2000 },
        { duration: STEADY_DURATION, target: 2000 },
        { duration: '2m', target: 0 },
      ],
      gracefulRampDown: '30s',
    },
    durable_commands: {
      executor: 'constant-arrival-rate',
      exec: 'durableCommand',
      rate: 50,
      timeUnit: '1s',
      duration: STEADY_DURATION,
      startTime: '5m',
      preAllocatedVUs: 100,
      maxVUs: 250,
    },
    realtime_events: {
      executor: 'constant-arrival-rate',
      exec: 'realtimeEvent',
      rate: 500,
      timeUnit: '1s',
      duration: STEADY_DURATION,
      startTime: '5m',
      preAllocatedVUs: 250,
      maxVUs: 750,
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.01'],
    liqi_api_latency_ms: ['p(95)<250', 'p(99)<750'],
    liqi_command_errors: ['rate<0.01'],
    liqi_event_errors: ['rate<0.01'],
    liqi_ws_connect_errors: ['rate<0.01'],
    dropped_iterations: ['count==0'],
  },
};

function required(name, value) {
  if (!value) {
    throw new Error(`${name} is required; the load plane does not invent provider endpoints or payload semantics`);
  }
}

function headers(extra = {}) {
  const result = {
    'Content-Type': 'application/json',
    'X-LIQI-Release-ID': RELEASE_ID,
    'X-LIQI-Load-Run': __ENV.LIQI_LOAD_RUN_ID || `k6-${RELEASE_ID}`,
    ...extra,
  };
  if (AUTH_TOKEN) {
    result.Authorization = `Bearer ${AUTH_TOKEN}`;
  }
  return result;
}

export function setup() {
  required('LIQI_BASE_URL', BASE_URL);
  required('LIQI_WS_URL', WS_URL);
  required('LIQI_RELEASE_ID', RELEASE_ID);
  required('LIQI_COMMAND_BODY_JSON', COMMAND_BODY);
  required('LIQI_EVENT_BODY_JSON', EVENT_BODY);
  return { releaseId: RELEASE_ID };
}

export function durableCommand() {
  const started = Date.now();
  const response = http.post(`${BASE_URL}${COMMAND_PATH}`, COMMAND_BODY, {
    headers: headers({ 'Idempotency-Key': `k6-${exec.vu.idInTest}-${exec.scenario.iterationInTest}` }),
    tags: { plane: 'command' },
  });
  apiLatency.add(Date.now() - started, { plane: 'command' });
  const ok = check(response, { 'durable command accepted or explicitly capacity-rejected': (r) => (r.status >= 200 && r.status < 300) || r.status === 429 || r.status === 503 });
  commandErrors.add(!ok || rIsUnexpected(response.status));
}

export function realtimeEvent() {
  const started = Date.now();
  const response = http.post(`${BASE_URL}${EVENT_PATH}`, EVENT_BODY, {
    headers: headers(),
    tags: { plane: 'realtime-producer' },
  });
  apiLatency.add(Date.now() - started, { plane: 'realtime-producer' });
  const ok = check(response, { 'realtime producer accepted or explicitly capacity-rejected': (r) => (r.status >= 200 && r.status < 300) || r.status === 429 || r.status === 503 });
  eventErrors.add(!ok || rIsUnexpected(response.status));
}

function rIsUnexpected(status) {
  return !((status >= 200 && status < 300) || status === 429 || status === 503);
}

export function websocketSession() {
  const socket = new WebSocket(WS_URL, [], { headers: headers() });
  let opened = false;
  socket.addEventListener('open', () => {
    opened = true;
    wsOpened.add(1);
    if (WS_HELLO) socket.send(WS_HELLO);
    if (exec.vu.idInTest <= ACTIVE_SUBSCRIPTIONS && WS_SUBSCRIBE) socket.send(WS_SUBSCRIBE);
  });
  socket.addEventListener('message', () => wsMessages.add(1));
  socket.addEventListener('error', () => wsConnectErrors.add(true));
  socket.addEventListener('close', () => wsConnectErrors.add(!opened));
  setTimeout(() => socket.close(1000, 'floor-run-complete'), WS_HOLD_MS);
}

export function handleSummary(data) {
  const path = __ENV.LIQI_K6_SUMMARY || 'summary-v1-floor.json';
  return { [path]: JSON.stringify(data, null, 2), stdout: `k6 V1 floor summary written to ${path}\n` };
}
