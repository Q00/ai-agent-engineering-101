// Parse explicit purchase intent, not arbitrary numbers in budgets, ratings or order IDs.
export function purchaseIntent(text, parts = []) {
  const raw = text.trim().replace(/[’‘]/g, "'");
  if (/\b(?:not|don't|do not|cannot|can't|if|unless)\b/i.test(raw)) return {offer:null,accept:false};
  for (const part of parts) {
    const data = part.data;
    if (data && typeof data === 'object' && /^(offer|buy|purchase|accept)$/i.test(data.action || '')) {
      const value = data.offer ?? data.price;
      if (typeof value === 'number' && Number.isSafeInteger(value)) return {offer:value,accept:false};
      return {offer:null,accept:false};
    }
  }
  const patterns = [
    /^([+-]?\d+(?:\.\d+)?)\s*(?:credits)?[.!]?$/i,
    /\b(?:offer|pay|for|price(?: is)?)\s*:?\s*([+-]?\d+(?:\.\d+)?)(?!\d|\.\d)/i,
    /\b(?:accept|agree to|buy at|take it at|take (?:the )?(?:lamp|chair|bookshelf) at)\s*(?:the (?:price|offer) of\s*)?([+-]?\d+(?:\.\d+)?)(?!\d|\.\d)/i,
    /\b(?:accept|buy|take|purchase)\b[^.!?\n]*?\bat\s+([+-]?\d+(?:\.\d+)?)(?!\d|\.\d)/i,
  ];
  for (const pattern of patterns) {
    const match = raw.match(pattern);
    if (match) {
      const n = Number(match[1]);
      return {offer:Number.isSafeInteger(n) ? n : null,accept:false};
    }
  }
  const accept = /^(?:i accept(?: (?:your|the) (?:price|offer|quote))?|accept|ok|okay|yes|deal|agreed|i agree|i'll take it)[.!]?$/i.test(raw);
  return {offer:null,accept};
}
