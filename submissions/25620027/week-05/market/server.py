"""Bearer-gated MCP Streamable HTTP market with local runner admin routes."""

from __future__ import annotations

import hmac
import os
import secrets
from typing import TYPE_CHECKING, Annotated, Final

import typer
import uvicorn
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import AnyHttpUrl, Field
from starlette.responses import JSONResponse

from market.api_models import NegotiationPath, OpenedNegotiation, OpenNegotiation
from market.engine import MarketStore
from market.engine_errors import MarketError
from market.models import FrozenModel, Grant, NegotiationId, Role
from market.tokens import InvalidTokenError, TokenCodec

if TYPE_CHECKING:
    from starlette.requests import Request

DEFAULT_BASE_URL: Final = "http://127.0.0.1:8001"
POLICY_ID: Final = "mirror-v1"
MIN_SECRET_LENGTH: Final = 16


class ServerSettings(FrozenModel):
    """Secrets and public addresses for one local experiment server."""

    base_url: str
    issuer_url: str
    token_secret: bytes
    admin_secret: str


class PartyTokenVerifier:
    """Verify HMAC grants and expose the authenticated subject to MCP."""

    def __init__(self, codec: TokenCodec, resource: str) -> None:
        self._codec: TokenCodec = codec
        self._resource: str = resource

    async def verify_token(self, token: str) -> AccessToken | None:
        """Return one SDK access token or reject invalid bearer input."""
        try:
            grant = self._codec.decode(token)
        except InvalidTokenError:
            return None
        return AccessToken(
            token=token,
            client_id=grant.role.value,
            scopes=["negotiate"],
            resource=self._resource,
            subject=grant.subject,
            claims=grant.model_dump(mode="json"),
        )


class MarketApplication:
    """Bind the deterministic store to MCP tools and admin HTTP routes."""

    def __init__(self, settings: ServerSettings) -> None:
        self.settings: ServerSettings = settings
        self.store: MarketStore = MarketStore()
        self.codec: TokenCodec = TokenCodec(settings.token_secret)
        resource = f"{settings.base_url}/mcp"
        verifier = PartyTokenVerifier(self.codec, resource)
        self.mcp: MCPServer[None] = MCPServer(
            "mirror-market",
            description="Negotiation market with shadow and enforced price policies.",
            version="0.1.0",
            token_verifier=verifier,
            auth=AuthSettings(
                issuer_url=AnyHttpUrl(settings.issuer_url),
                resource_server_url=AnyHttpUrl(resource),
                required_scopes=["negotiate"],
                validate_token_resource=True,
            ),
        )
        self._register_tools()
        self._register_admin_routes()

    def _grant(self) -> Grant:
        token = get_access_token()
        if token is None:
            message = "authenticated bearer grant is missing"
            raise ToolError(message)
        try:
            return self.codec.decode(token.token)
        except InvalidTokenError as exc:
            message = "authenticated bearer grant is invalid"
            raise ToolError(message) from exc

    def _register_tools(self) -> None:
        @self.mcp.tool()
        def get_negotiation(negotiation_id: str) -> str:
            """Read the item, caller role, turn, status, and committed moves."""
            try:
                view = self.store.view(self._grant(), NegotiationId(negotiation_id))
            except MarketError as exc:
                raise ToolError(exc.detail) from exc
            return view.model_dump_json()

        @self.mcp.tool()
        def propose(negotiation_id: str, price: Annotated[int, Field(ge=0)]) -> str:
            """Offer a whole-number price; a valid proposal ends the caller's turn."""
            try:
                self.store.propose(self._grant(), NegotiationId(negotiation_id), price)
            except MarketError as exc:
                raise ToolError(exc.detail) from exc
            return f"proposal committed at {price}"

        @self.mcp.tool()
        def accept_proposal(negotiation_id: str) -> str:
            """Accept the other party's latest proposal and close with a deal."""
            try:
                self.store.accept(self._grant(), NegotiationId(negotiation_id))
            except MarketError as exc:
                raise ToolError(exc.detail) from exc
            return "proposal accepted; negotiation closed with a deal"

        @self.mcp.tool()
        def reject_proposal(negotiation_id: str) -> str:
            """Reject the other party's latest proposal and end the caller's turn."""
            try:
                self.store.reject(self._grant(), NegotiationId(negotiation_id))
            except MarketError as exc:
                raise ToolError(exc.detail) from exc
            return "proposal rejected; negotiation remains open"

        @self.mcp.tool()
        def refuse(negotiation_id: str) -> str:
            """Leave this negotiation and close it with no deal."""
            try:
                self.store.refuse(self._grant(), NegotiationId(negotiation_id))
            except MarketError as exc:
                raise ToolError(exc.detail) from exc
            return "party refused; negotiation closed with no deal"

    def _register_admin_routes(self) -> None:
        @self.mcp.custom_route("/health", methods=["GET"])
        async def health(_request: Request) -> JSONResponse:
            return JSONResponse({"status": "ok"})

        @self.mcp.custom_route("/admin/negotiations", methods=["POST"])
        async def open_negotiation(request: Request) -> JSONResponse:
            denied = self._authorize_admin(request)
            if denied is not None:
                return denied
            payload = OpenNegotiation.model_validate_json(await request.body())
            negotiation_id = self.store.open(payload.scenario, payload.condition)
            opened = OpenedNegotiation(
                negotiation_id=negotiation_id,
                buyer_token=self._mint(Role.BUYER, negotiation_id, payload),
                seller_token=self._mint(Role.SELLER, negotiation_id, payload),
            )
            return JSONResponse(opened.model_dump(mode="json"), status_code=201)

        @self.mcp.custom_route("/admin/negotiations/{negotiation_id}/summary", methods=["GET"])
        async def summary(request: Request) -> JSONResponse:
            denied = self._authorize_admin(request)
            if denied is not None:
                return denied
            path = NegotiationPath.model_validate(request.path_params)
            negotiation_id = NegotiationId(path.negotiation_id)
            try:
                result = self.store.summary(negotiation_id)
            except MarketError as exc:
                return JSONResponse({"error": exc.detail}, status_code=404)
            return JSONResponse(result.model_dump(mode="json"))

        @self.mcp.custom_route("/admin/negotiations/{negotiation_id}/events", methods=["GET"])
        async def events(request: Request) -> JSONResponse:
            denied = self._authorize_admin(request)
            if denied is not None:
                return denied
            path = NegotiationPath.model_validate(request.path_params)
            negotiation_id = NegotiationId(path.negotiation_id)
            try:
                records = self.store.events(negotiation_id)
            except MarketError as exc:
                return JSONResponse({"error": exc.detail}, status_code=404)
            return JSONResponse([record.model_dump(mode="json") for record in records])

        @self.mcp.custom_route("/admin/negotiations/{negotiation_id}/pass", methods=["POST"])
        async def pass_turn(request: Request) -> JSONResponse:
            denied = self._authorize_admin(request)
            if denied is not None:
                return denied
            path = NegotiationPath.model_validate(request.path_params)
            negotiation_id = NegotiationId(path.negotiation_id)
            try:
                self.store.pass_turn(negotiation_id)
            except MarketError as exc:
                return JSONResponse({"error": exc.detail}, status_code=409)
            return JSONResponse({"status": "passed"})

    def _mint(
        self,
        role: Role,
        negotiation_id: NegotiationId,
        opened: OpenNegotiation,
    ) -> str:
        limit = opened.scenario.budget if role is Role.BUYER else opened.scenario.reserve
        grant = Grant(
            subject=f"{role.value}-{secrets.token_hex(6)}",
            role=role,
            negotiation_id=negotiation_id,
            limit=limit,
            condition=opened.condition,
            policy_id=POLICY_ID,
            nonce=secrets.token_hex(12),
        )
        return self.codec.mint(grant)

    def _authorize_admin(self, request: Request) -> JSONResponse | None:
        provided = request.headers.get("x-admin-secret", "")
        if hmac.compare_digest(provided, self.settings.admin_secret):
            return None
        return JSONResponse({"error": "admin authorization required"}, status_code=401)


def settings_from_env(base_url: str) -> ServerSettings:
    """Parse local-only secrets without committing them to the repository."""
    token_secret = os.environ.get("MIRROR_MARKET_TOKEN_SECRET", "")
    admin_secret = os.environ.get("MIRROR_MARKET_ADMIN_SECRET", "")
    if len(token_secret) < MIN_SECRET_LENGTH or len(admin_secret) < MIN_SECRET_LENGTH:
        message = "set 16+ character token and admin secrets"
        raise typer.BadParameter(message)
    issuer = os.environ.get("MIRROR_MARKET_ISSUER", "https://auth.invalid/")
    return ServerSettings(
        base_url=base_url,
        issuer_url=issuer,
        token_secret=token_secret.encode("utf-8"),
        admin_secret=admin_secret,
    )


def main(
    host: str = "127.0.0.1",
    port: int = 8001,
) -> None:
    """Run the market over MCP Streamable HTTP."""
    base_url = f"http://{host}:{port}"
    application = MarketApplication(settings_from_env(base_url))
    app = application.mcp.streamable_http_app(json_response=True, stateless_http=True, host=host)
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    typer.run(main)
