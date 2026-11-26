import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {formatOperatingPoint} from '../frontend/number-format.js';

assert.equal(formatOperatingPoint(127.99036), '127,99');
assert.equal(formatOperatingPoint(49.70857), '49,71');
assert.equal(formatOperatingPoint(2), '2,00');
assert.equal(formatOperatingPoint(0), '0,00');
assert.equal(formatOperatingPoint(-1.236), '-1,24');
assert.equal(formatOperatingPoint(Number.NaN), '—');

console.log('Operating-point formatting: fixed 2 decimals, Russian locale, rounding — OK');

const dashboard = readFileSync(new URL('../frontend/dashboard.js', import.meta.url), 'utf8');
assert.match(dashboard, /flow\.textContent\s*=\s*formatOperatingPoint\(point\.q_liq\)/);
assert.match(dashboard, /pressure\.textContent\s*=\s*formatOperatingPoint\(point\.p_wf\)/);
const nginx = readFileSync(new URL('../services/backend/nginx.conf', import.meta.url), 'utf8');
assert.match(nginx, /Cache-Control\s+"no-store, no-cache/);

console.log('Dashboard metrics use the 2-decimal formatter; frontend assets are not cached — OK');
