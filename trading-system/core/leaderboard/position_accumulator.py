"""
Minimal net-position accumulator — the safeguard gate a wallet-copy buy
event must clear before it's allowed to become a dispatchable trigger.

Guards against the specific risk the leaderboard/copy-trading feature
exists to worry about: a tracked wallet's buy looking like fresh
accumulation when it's actually a small top-up inside a larger recent
distribution of the SAME token — i.e. they're selling into the exact pump
we'd be copying.

Scope is deliberately narrow — this is NOT full position tracking (that's
an explicit later phase). It only answers one question: "over the last
window, is this wallet net-accumulating this specific token, or net
distributing it?"

Weighting is raw signed token count, not USD value. That's an accepted
limitation, not an oversight: this can't tell you a wallet dumped $2M and
is now buying back $50 worth of the same token — it only sees token units.
USD-denominated weighting is deferred future scope.

Callers MUST call register_wallet() at wallet-onboarding time, before any
record_and_check() call for that wallet — see register_wallet()'s
docstring for why.
"""
import json
import os
import time

WINDOW_SECONDS = 24 * 3600  # trailing window a token's net position is computed over

STATE_FILE = "logs/wallet_position_ledger.json"

# {"wallet_tracking_started_at": {wallet: epoch_ts}, "ledger": {"wallet|mint": [[ts, signed_amount], ...]}}
_state = {"wallet_tracking_started_at": {}, "ledger": {}}
_loaded = False


def _key(wallet: str, token_mint: str) -> str:
    return f"{wallet}|{token_mint}"


def _load():
    global _state, _loaded
    if _loaded:
        return
    _loaded = True
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE, 'r') as f:
            raw = json.load(f)
        _state["wallet_tracking_started_at"] = raw.get("wallet_tracking_started_at", {})
        _state["ledger"] = raw.get("ledger", {})
    except Exception:
        pass  # keep the empty default state over a corrupt file


def _save():
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, 'w') as f:
        json.dump(_state, f, indent=2)


def _prune(key: str, now: float) -> list:
    entries = _state["ledger"].get(key, [])
    entries = [[ts, amt] for ts, amt in entries if now - ts <= WINDOW_SECONDS]
    _state["ledger"][key] = entries
    return entries


def register_wallet(wallet: str, started_at: float = None, now: float = None) -> None:
    """
    Marks when we began monitoring `wallet`, decoupled from any specific
    event. Call this once, at wallet-onboarding time (e.g. when the wallet
    is added to the Helius webhook's accountAddresses config) — NOT lazily
    from the first event that happens to arrive for it.

    This matters: record_and_check() used to set the tracking-start clock
    itself on a wallet's first-ever call, which meant "tracking start" and
    "first observed event" were always the same timestamp — so cold-start
    failed on literally every wallet's true first buy, permanently
    defeating the safeguard's own core use case. Registering separately,
    ahead of time, lets a wallet's genuine first buy arrive after the
    tracking window is already satisfied.

    Idempotent — does nothing if the wallet is already registered, so a
    re-registration (e.g. on bot restart, if onboarding logic reruns)
    can't reset an already-aging clock.
    """
    _load()
    if wallet in _state["wallet_tracking_started_at"]:
        return
    _state["wallet_tracking_started_at"][wallet] = (
        started_at if started_at is not None else (now if now is not None else time.time())
    )
    _save()


def record_and_check(wallet: str, token_mint: str, signed_amount: float, now: float = None) -> dict:
    """
    Records one event (positive signed_amount = buy, negative = sell) for
    this (wallet, token_mint) pair, then checks the accumulation gate.

    `now` is injectable for testing time-windowed behavior without real
    sleeping; production callers should omit it (defaults to time.time()).

    Returns:
      {
        'passed': bool,          # True only if past cold-start AND net-positive
        'reason': str,
        'cold_start': bool,      # True if wallet hasn't been tracked a full window yet
        'net_position': float,   # signed sum over the window, including this event
        'wallet_tracking_seconds': float,
      }
    """
    _load()
    now = now if now is not None else time.time()

    tracking_started = _state["wallet_tracking_started_at"].get(wallet)
    if tracking_started is None:
        # Defensive fallback only — normal flow should always call
        # register_wallet() before the first event for a wallet arrives.
        # Auto-registering here still fails closed via cold-start rather
        # than crashing, but shouldn't happen in normal operation.
        print(f"[!] position_accumulator: {wallet} received an event before "
              f"being registered — auto-registering now (investigate if seen "
              f"in normal operation, this should always be pre-registered)")
        register_wallet(wallet, now=now)
        tracking_started = now

    key = _key(wallet, token_mint)
    entries = _prune(key, now)
    entries.append([now, signed_amount])
    _state["ledger"][key] = entries
    _save()

    tracking_seconds = now - tracking_started
    cold_start = tracking_seconds < WINDOW_SECONDS

    net_position = sum(amt for _, amt in entries)

    if cold_start:
        return {
            "passed": False,
            "reason": (f"Cold start — wallet tracked for only "
                       f"{tracking_seconds / 3600:.1f}h of the required "
                       f"{WINDOW_SECONDS / 3600:.0f}h window; recent history "
                       f"before tracking began is unobserved, so net position "
                       f"can't be trusted yet"),
            "cold_start": True,
            "net_position": net_position,
            "wallet_tracking_seconds": tracking_seconds,
        }

    passed = net_position > 0
    reason = (f"Net position {net_position:+.4f} tokens over {WINDOW_SECONDS / 3600:.0f}h "
              f"window — {'still net-accumulating' if passed else 'net distributing, not accumulating'}")

    return {
        "passed": passed,
        "reason": reason,
        "cold_start": False,
        "net_position": net_position,
        "wallet_tracking_seconds": tracking_seconds,
    }


if __name__ == "__main__":
    import json as _json

    print("[*] Testing net-position accumulator...")

    # Use a throwaway state file so this self-test never touches real data.
    STATE_FILE = "logs/_test_wallet_position_ledger.json"
    if os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)
    _loaded = False
    _state = {"wallet_tracking_started_at": {}, "ledger": {}}

    wallet = "TestWallet111"
    mint = "TestMint222"
    t0 = time.time()

    print("\n=== Wallet registered 25h ago, this is their genuine first buy — should PASS ===")
    register_wallet(wallet, now=t0)
    r1 = record_and_check(wallet, mint, 1000.0, now=t0 + 25 * 3600)
    print(_json.dumps(r1, indent=2))
    assert r1["cold_start"] is False and r1["passed"] is True

    print("\n=== Different wallet, registered only 1h ago — still cold start regardless of net-positive ledger ===")
    wallet_new = "TestWalletNew444"
    register_wallet(wallet_new, now=t0)
    r2 = record_and_check(wallet_new, mint, 500.0, now=t0 + 3600)
    print(_json.dumps(r2, indent=2))
    assert r2["cold_start"] is True and r2["passed"] is False

    print("\n=== register_wallet() is idempotent — re-registering doesn't reset an aging clock ===")
    register_wallet(wallet, now=t0 + 999999)  # attempt to re-register much later
    r1b = record_and_check(wallet, mint, 1.0, now=t0 + 25 * 3600 + 1)
    print(_json.dumps(r1b, indent=2))
    assert r1b["cold_start"] is False  # would be True if re-registration had reset the clock

    print("\n=== New wallet+token pair, past cold start: sell exceeds prior buy — should fail net-distributing ===")
    wallet2 = "TestWallet333"
    register_wallet(wallet2, now=t0)
    record_and_check(wallet2, mint, 3000.0, now=t0 + 25 * 3600 + 100)        # buy, past cold start
    r4 = record_and_check(wallet2, mint, -4800.0, now=t0 + 25 * 3600 + 200)  # bigger sell right after
    print(_json.dumps(r4, indent=2))
    assert r4["cold_start"] is False and r4["passed"] is False

    print("\n=== Defensive fallback: an event for a never-registered wallet still fails closed, doesn't crash ===")
    r5 = record_and_check("NeverRegisteredWallet", mint, 999.0, now=t0)
    print(_json.dumps(r5, indent=2))
    assert r5["cold_start"] is True and r5["passed"] is False

    os.remove(STATE_FILE)
    print("\n[OK] All accumulator self-tests passed.")
