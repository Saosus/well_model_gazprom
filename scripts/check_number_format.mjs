import assert from 'node:assert/strict';
import {formatOperatingPoint} from '../frontend/number-format.js';

assert.equal(formatOperatingPoint(127.99036), '127,99');
assert.equal(formatOperatingPoint(49.70857), '49,71');
assert.equal(formatOperatingPoint(2), '2,00');
assert.equal(formatOperatingPoint(0), '0,00');
assert.equal(formatOperatingPoint(-1.236), '-1,24');
assert.equal(formatOperatingPoint(Number.NaN), '—');

console.log('Operating-point formatting: fixed 2 decimals, Russian locale, rounding — OK');
