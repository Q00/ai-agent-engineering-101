"""Local participation restrictions, separate from public reputation and pricing.

The current market transport does not authenticate buyer names. Metadata matches
are self-declarations, and anonymous calls cannot be attributed to a registry buyer.
"""
from pathlib import Path
import json


class BuyerAccess:
    def __init__(self, path=None):
        path = Path(path or Path(__file__).resolve().parent / "data" / "buyer-access.json")
        policy = json.loads(path.read_text())
        self.version = policy["version"]
        self.denied = {}
        for entry in policy["blocked_buyers"]:
            name = entry["registry_name"]
            if not isinstance(name, str) or not name.strip():
                raise ValueError("Blocked buyer must have a registry name")
            self.denied[name.strip().casefold()] = name

    def match(self, *, user=None, message_metadata=None, request_metadata=None):
        """Match only explicit identity fields, never product text or reviews."""
        if user is not None and user.is_authenticated:
            # A verified principal takes precedence over untrusted claims.
            return self._match_name(user.user_name, "authenticated_user", True)
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
        return None

    def _match_name(self, value, source, authenticated):
        if isinstance(value, str):
            name = self.denied.get(value.strip().casefold())
            if name:
                return {"buyer": name, "source": source, "authenticated": authenticated}
        return None
