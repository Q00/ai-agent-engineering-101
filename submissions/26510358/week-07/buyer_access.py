"""Local participation restrictions, separate from public reputation and pricing.

The current market transport does not authenticate buyer names. Metadata matches
are self-declarations, and anonymous calls cannot be attributed to a registry buyer.
"""
from pathlib import Path
import json


class BuyerAccess:
    def __init__(self, path=None):
        self.path = Path(path or Path(__file__).resolve().parent / "data" / "buyer-access.json")
        self._reload()

    def _reload(self):
        # Read fresh restrictions on each turn. Invalid configuration raises
        # before quoting/signing instead of silently disabling restrictions.
        policy = json.loads(self.path.read_text())
        denied, denied_owners = {}, {}
        for entry in policy["blocked_buyers"]:
            name = entry["registry_name"]
            if not isinstance(name, str) or not name.strip():
                raise ValueError("Blocked buyer must have a registry name")
            name = name.strip()
            denied[name.casefold()] = name
            owner = entry.get("owner_login")
            if owner is not None:
                if not isinstance(owner, str) or not owner.strip():
                    raise ValueError("Blocked buyer owner must have a GitHub login")
                denied_owners[owner.strip().casefold()] = name
        self.version = policy["version"]
        self.denied, self.denied_owners = denied, denied_owners

    def match(self, *, user=None, message_metadata=None, request_metadata=None):
        """Match only explicit identity fields, never product text or reviews."""
        self._reload()
        if user is not None and user.is_authenticated:
            # A verified principal takes precedence over untrusted claims.
            return (self._match_name(user.user_name, "authenticated_user", True)
                    or self._match_owner(user.user_name, "authenticated_user", True))
        for label, metadata in (("message.metadata", message_metadata),
                                ("request.metadata", request_metadata)):
            if not isinstance(metadata, dict):
                continue
            for key in ("buyerName", "buyer_name", "buyer"):
                value = metadata.get(key)
                if key == "buyer" and isinstance(value, dict):
                    value = value.get("name")
                match = self._match_name(value, label + "." + key, False)
                if match:
                    return match
            # Only buyer-specific owner fields count; an unrelated top-level
            # owner or mentions in product/review text do not identify a buyer.
            for key in ("buyerOwnerLogin", "buyer_owner_login"):
                match = self._match_owner(metadata.get(key), label + "." + key, False)
                if match:
                    return match
            buyer = metadata.get("buyer")
            if isinstance(buyer, dict):
                owner = buyer.get("owner")
                login = owner.get("login") if isinstance(owner, dict) else None
                match = self._match_owner(login, label + ".buyer.owner.login", False)
                if match:
                    return match
        return None

    def _match_name(self, value, source, authenticated):
        return self._match(value, self.denied, source, authenticated)

    def _match_owner(self, value, source, authenticated):
        return self._match(value, self.denied_owners, source, authenticated)

    @staticmethod
    def _match(value, identities, source, authenticated):
        if isinstance(value, str):
            name = identities.get(value.strip().casefold())
            if name:
                return {"buyer": name, "source": source, "authenticated": authenticated}
        return None
