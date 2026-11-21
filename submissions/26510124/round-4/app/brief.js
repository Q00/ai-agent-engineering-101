/* Pure input contract, shared by the page tool and Node tests. */
(function (scope) {
  'use strict';
  const choices = new Set(['balanced', 'price', 'quality']);
  function normalize(input, now = new Date().toISOString()) {
    const raw = String(input.budget ?? '').trim();
    if (!/^\d+$/.test(raw)) throw new Error('예산은 0 이상의 정수로 입력해 주세요.');
    const budget = Number(raw);
    if (!Number.isSafeInteger(budget) || budget > 100000000) throw new Error('예산은 1억 크레딧 이하로 입력해 주세요.');
    const interests = String(input.interests ?? '').trim();
    if (interests.length > 120) throw new Error('관심 상품은 120자 이내로 입력해 주세요.');
    if (!choices.has(input.priority)) throw new Error('선택 기준을 확인해 주세요.');
    return {status: 'saved', budget_credits: budget,
      interests: [...new Set(interests.split(',').map(x => x.trim()).filter(Boolean))],
      priority: input.priority, updated_at: now, is_payment_authorization: false};
  }
  function unset() {
    return {status: 'not_set', budget_credits: null, interests: [], priority: 'balanced',
      updated_at: null, is_payment_authorization: false};
  }
  function restore(value) {
    try {
      const obj = JSON.parse(value);
      if (obj.status !== 'saved' || !Array.isArray(obj.interests)) return unset();
      return normalize({budget: obj.budget_credits, interests: obj.interests.join(', '), priority: obj.priority}, obj.updated_at);
    } catch (_) { return unset(); }
  }
  const api = {normalize, unset, restore};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else scope.ShopBrief = api;
})(globalThis);
