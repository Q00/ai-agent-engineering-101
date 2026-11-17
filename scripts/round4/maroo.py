"""The market's chain adapter: Maroo testnet wallets, signatures and payments. Nothing else here knows about the chain.

Wallets are EVM keys, so a key file imports into any EVM wallet. Testnet only: never put a mainnet key in a key file.
Signatures are EIP-191 personal messages (65 bytes, recoverable). The address is the public identity.
Credits stay the market's unit; CREDIT_WEI converts them. Each side of a deal pays a market fee and half of a standard
network fee (fees()). The buyer advances the real gas and takes the shop's share out of the shop's transfer.
"""
import base64
import os
import re
import time
from pathlib import Path

import httpx
from eth_account import Account
from eth_account.messages import encode_defunct
from eth_utils import to_checksum_address

RPC_URL = os.environ.get("MAROO_RPC_URL", "https://rpc-testnet.maroo.io")
CHAIN_ID = int(os.environ.get("MAROO_CHAIN_ID", "450815"))
EXPLORER = os.environ.get("MAROO_EXPLORER_URL", "https://explorer-testnet.maroo.io").rstrip("/")
FAUCET = os.environ.get("MAROO_FAUCET_URL", "https://faucet.maroo.io")
CREDIT_WEI = int(os.environ.get("MAROO_CREDIT_WEI", str(10 ** 15)))   # 1,000 credits = 1 tOKRW
MARKET_FEE_BPS = int(os.environ.get("MARKET_FEE_BPS", "100"))         # each side pays this share of the price to the market
NETWORK_FEE_CREDITS = int(os.environ.get("MARKET_NETWORK_FEE_CREDITS", "420"))   # about two transfers, shared by the two sides
GAS_RESERVE = 10 ** 18                                                 # keep this much for gas when checking a balance
RECEIPT_WAIT = 90                                                      # seconds to wait for a transfer to land
ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}")
TX_HASH = re.compile(r"0x[0-9a-fA-F]{64}")


class Wallet:
    def __init__(self, account):
        self.account = account
        self.address = account.address

    @classmethod
    def generate(cls):
        return cls(Account.create())

    @classmethod
    def from_hex(cls, key):
        return cls(Account.from_key(key.strip()))

    @classmethod
    def load(cls, path):
        """The wallet in a key file (hex), created when the file does not exist."""
        path = Path(path)
        if not path.exists():
            path.write_text(Account.create().key.hex() + "\n", encoding="utf-8")
        if os.name != "nt":
            os.chmod(path, 0o600)
        return cls.from_hex(path.read_text(encoding="utf-8"))

    def key_hex(self):
        return self.account.key.hex()

    def sign(self, message: bytes) -> bytes:
        return bytes(self.account.sign_message(encode_defunct(primitive=message)).signature)


def normalize_address(address):
    """The checksummed form of an address, or None."""
    if not isinstance(address, str) or not ADDRESS.fullmatch(address):
        return None
    return to_checksum_address(address)


def verify_bytes(address, signature: bytes, message: bytes):
    try:
        return Account.recover_message(encode_defunct(primitive=message), signature=signature) == address
    except Exception:                                  # a malformed signature is simply not a valid one
        return False


def verify(address, sig_b64, message: bytes):
    try:
        return verify_bytes(address, base64.b64decode(sig_b64), message)
    except (ValueError, TypeError):
        return False


def fees(price, bps=MARKET_FEE_BPS, network=NETWORK_FEE_CREDITS):
    """What each side of a deal pays, in credits. shop and buyer are equal; market goes to the market's wallet."""
    each = price * bps // 10000
    return {"shop": each + network // 2, "buyer": each + network // 2, "market": each * 2}


def tx_url(tx_hash):
    return f"{EXPLORER}/tx/{tx_hash}"


def info():
    return {"provider": "maroo", "network": "maroo-testnet", "chain_id": CHAIN_ID, "asset": "tOKRW", "rpc": RPC_URL,
            "explorer": EXPLORER, "faucet": FAUCET, "credit_wei": CREDIT_WEI, "market_fee_bps": MARKET_FEE_BPS,
            "network_fee_credits": NETWORK_FEE_CREDITS}


def _body(method, params):
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}


def _result(response):
    response.raise_for_status()
    j = response.json()
    if j.get("error"):
        raise RuntimeError(f"maroo rpc: {j['error'].get('message', j['error'])}")
    return j.get("result")


def rpc(method, *params, timeout=20):
    return _result(httpx.post(RPC_URL, json=_body(method, list(params)), timeout=timeout))


def balance(address):
    return int(rpc("eth_getBalance", address, "latest"), 16)


def pay(wallet, to, wei, data: bytes = b""):
    """Send wei to an address and wait for the receipt. Returns the transaction hash; raises when it did not succeed."""
    tx = {"from": wallet.address, "to": to, "value": hex(wei), "data": "0x" + data.hex()}
    gas = int(rpc("eth_estimateGas", tx), 16)
    raw = wallet.account.sign_transaction({
        "chainId": CHAIN_ID, "nonce": int(rpc("eth_getTransactionCount", wallet.address, "pending"), 16),
        "to": to, "value": wei, "data": data, "gas": gas * 12 // 10,
        "gasPrice": int(rpc("eth_gasPrice"), 16)}).raw_transaction
    tx_hash = rpc("eth_sendRawTransaction", "0x" + bytes(raw).hex())
    deadline = time.time() + RECEIPT_WAIT
    while time.time() < deadline:
        try:
            receipt = rpc("eth_getTransactionReceipt", tx_hash)
        except (httpx.HTTPError, RuntimeError, ValueError):   # the transfer is sent; a node may not have indexed it yet
            receipt = None
        if receipt:
            if int(receipt.get("status", "0x0"), 16) != 1:
                raise RuntimeError(f"transfer {tx_hash} failed on chain")
            return tx_hash
        time.sleep(1)
    raise TimeoutError(f"transfer {tx_hash} has no receipt after {RECEIPT_WAIT} s")


async def payment_problem(tx_hash, payer, payee, wei, data: bytes):
    """Why a transaction is not this payment, or None when it is. Raises httpx or RuntimeError when the chain cannot say."""
    if not isinstance(tx_hash, str) or not TX_HASH.fullmatch(tx_hash):
        return "no transaction hash"
    async with httpx.AsyncClient(timeout=15) as c:
        tx = _result(await c.post(RPC_URL, json=_body("eth_getTransactionByHash", [tx_hash])))
        receipt = _result(await c.post(RPC_URL, json=_body("eth_getTransactionReceipt", [tx_hash])))
    if not tx or not receipt:
        return "the transaction is not on chain"
    if int(receipt.get("status", "0x0"), 16) != 1:
        return "the transaction failed"
    if normalize_address(tx.get("from")) != payer:
        return "the payer is not the order's buyer"
    if normalize_address(tx.get("to")) != payee:
        return "the payee is not this shop"
    if int(tx.get("value", "0x0"), 16) != wei:
        return "the amount is not the checkout total"
    if (tx.get("input") or tx.get("data") or "0x").lower() != "0x" + data.hex():
        return "the transaction does not name this checkout"
    return None
