const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const brief = require('../app/brief.js');

test('page tool returns saved conditions without authorizing payments', () => {
  const result = brief.normalize({budget: '30000', interests: '우유, 생수, 우유', priority: 'balanced'}, 'fixture-time');
  assert.deepEqual(result.interests, ['우유', '생수']);
  assert.equal(result.budget_credits, 30000);
  assert.equal(result.is_payment_authorization, false);
  assert.equal(result.status, 'saved');
});
test('reject invalid budgets and preferences; zero is valid', () => {
  for (const budget of ['', '-1', '1.5', '1e3', 'Infinity', '100000001', '9007199254740993']) {
    assert.throws(() => brief.normalize({budget, priority: 'balanced'}));
  }
  assert.equal(brief.normalize({budget: '0', priority: 'price'}).budget_credits, 0);
  assert.throws(() => brief.normalize({budget: '1', priority: 'pay_now'}));
  assert.throws(() => brief.normalize({budget: '1', priority: 'balanced', interests: 'a'.repeat(121)}));
});
test('restore validates saved input and never restores payment authorization', () => {
  assert.equal(brief.restore('broken').status, 'not_set');
  assert.equal(brief.restore(null).budget_credits, null);
  const saved = brief.normalize({budget: '500', priority: 'quality', interests: '침구'});
  saved.is_payment_authorization = true;
  assert.equal(brief.restore(JSON.stringify(saved)).is_payment_authorization, false);
  saved.budget_credits = -3;
  assert.equal(brief.restore(JSON.stringify(saved)).status, 'not_set');
});
test('page keeps required SDK and mobile install metadata', () => {
  const root = path.resolve(__dirname, '..');
  const html = fs.readFileSync(path.join(root, 'app/index.html'), 'utf8');
  for (const token of ['/miniapp.js', 'viewport-fit=cover', 'manifest.webmanifest', 'theme-color', 'apple-touch-icon']) {
    assert.ok(html.includes(token), token);
  }
  const app = fs.readFileSync(path.join(root, 'app/app.js'), 'utf8');
  assert.ok(app.includes("miniapp.offer('shopping_brief'"));
  assert.ok(!app.includes('.innerHTML'), 'untrusted chat and tool text uses textContent');
  assert.ok(app.includes('isComposing'), 'IME composition must not send a partial Korean message');
});
