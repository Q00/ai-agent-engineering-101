"""Default-off deep-lowball experiment; no pricing or inventory policy changes."""
import time
import hashlib
import fast_classify
import negotiate
from seller_reader_trial import template_offer


def deep_lowball(text, deal, floor, max_rounds=10, unit_ok=False):
    if unit_ok is not True or not isinstance(text, str):
        return None
    if deal.get("kind") not in ("food", "goods"):
        return None
    if not isinstance(deal.get("unit"), str) or not deal["unit"]:
        return None
    if any(type(v) is not int or v <= 0 for v in
           (floor, deal.get("ask"), deal.get("reserve"),
            deal.get("rounds"), max_rounds)):
        return None
    if deal["rounds"] >= max_rounds or not negotiate.engine_on():
        return None
    if fast_classify.bare_offer(text) is not None:
        return None
    offer = template_offer(text)
    least = max(floor, deal["reserve"])
    if offer is None or not (0 < offer < least < deal["ask"] and
                             offer * 2 < deal["ask"]):
        return None
    result = fast_classify.seller_turn(str(offer), deal, floor, None)
    if not result or result.get("action") != "counter" or result.get("price") != deal["ask"]:
        return None
    return result

from seller_opening_cue import OpeningCue
from at_floor_exact_trial import AtFloorExactTrial


class DeepLowballTrial(OpeningCue):
    """Separate default-off enrollment; the adopted pricing schedule stays upstream."""
    def __init__(self, root=None):
        super().__init__(root)
        self.root = self.root.parent / 'deep_lowball'

    _snapshot = staticmethod(AtFloorExactTrial._snapshot)
    _existing = AtFloorExactTrial._existing

    def config(self):
        if (self.root / 'adopted').exists():
            self.invalid('deep_lowball_no_adoption')
            return None
        cfg = super().config()
        if cfg and not cfg.get('stop') and cfg['end'] - cfg['start'] != 1800:
            self.invalid('deep_lowball_fixed_window')
            return None
        return cfg

    def quote(self, task, deal, now=None, unit_ok=False, others=()):
        import time, uuid
        if unit_ok is not True or 'deep_lowball' in deal or deal.get('rounds', 0):
            return
        if deal.get('kind') not in ('food', 'goods') or not deal.get('unit'):
            return
        now = time.time() if now is None else now
        try:
            if not isinstance(task, str) or str(uuid.UUID(task)) != task:
                return
            old = self._existing(task)
            cfg = self.config()
            active = bool(cfg and not cfg.get('stop') and
                          cfg['start'] <= now < cfg['end'] and
                          not (self.root / 'disabled').exists())
            if old is None and (not active or (self.root / 'adopted').exists()):
                return
            if old is None and any(AtFloorExactTrial._other_active(c, now) for c in others):
                return
            if old is None and any(
                    isinstance(value, dict) and value.get('id') not in (None, 'adopted-v1')
                    for key,value in deal.items() if key in (
                        'reader_trial','concession_trial','late_concession','opening_cue',
                        'late_speed','jev_transport','shelf_life','exact_accept','exact_counter',
                        'grade_preference','food_opening','at_floor_exact')):
                return
            a = old if old is not None else self._assign(task, deal, now)
            if a.get('id') and any(a.get(k) != v for k,v in self._snapshot(deal).items()):
                a = dict(a, contaminated=True)
                self.invalid('deep_lowball_snapshot')
            deal['deep_lowball'] = a
            self.event(task, deal, 'quote', deal['ask'], now)
        except Exception:
            self.invalid('deep_lowball_quote')

    def arm(self, assignment):
        if assignment.get('contaminated') or not assignment.get('id'):
            return 'A'
        enrolled = assignment.get('enrolled_at')
        if type(enrolled) not in (int, float) or not enrolled <= time.time() < enrolled + 600:
            return 'A'
        return assignment.get('arm', 'A')

    def decide(self, text, deal, floor, max_rounds=10, unit_ok=False):
        deal['deep_lowball_eligible'] = False
        deal['deep_lowball_used'] = False
        deal['deep_lowball_input_sha'] = hashlib.sha256(text.encode()).hexdigest() if isinstance(text,str) else None
        a = deal.get('deep_lowball') or {}
        if a.get('id') and any(a.get(k) != v for k,v in self._snapshot(deal).items() if k != 'ask'):
            deal['deep_lowball'] = dict(a, contaminated=True)
            self.invalid('deep_lowball_snapshot')
            return None
        result = deep_lowball(text, deal, floor, max_rounds, unit_ok)
        deal['deep_lowball_eligible'] = result is not None
        if result is None or self.arm(a) != 'B':
            return None
        deal['deep_lowball_used'] = True
        return result

    def event(self, task, deal, event, price=None, now=None, **fields):
        from seller_concession_trial import Trial
        Trial.event(self, task, dict(deal,concession_trial=deal.get('deep_lowball') or {}),
                    event,price,now,kind=deal.get('kind'),expires_at=deal.get('expires_at'),
                    current_warranty=deal.get('warranty'),
                    deep_lowball_eligible=bool(deal.get('deep_lowball_eligible')),
                    deep_lowball_used=bool(deal.get('deep_lowball_used')),
                    input_sha256=deal.get('deep_lowball_input_sha'),**fields)

    def pre_pick_ok(self, others=(), now=None):
        """Reject conflicting trial configuration before any selection/enrollment."""
        now = time.time() if now is None else now
        if not AtFloorExactTrial._other_active(self, now):
            return True
        return not any(AtFloorExactTrial._other_active(c, now) for c in others)
