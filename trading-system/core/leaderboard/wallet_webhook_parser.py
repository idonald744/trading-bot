"""
Parses one Helius enhanced-webhook SWAP transaction into a structured buy
event for a tracked wallet.

Built and verified against a real captured payload (PUMP_FUN swap,
signature 49aJJJXK...), not written from the docs alone — Helius's docs
don't publicly specify the exact payload shape, which is exactly why this
needed empirical verification before being trusted.

Key finding from that verification: the wallet's own accountData entry
does NOT reliably carry its tokenBalanceChanges — in the real payload it
was empty, with the actual token movement recorded against a *different*
accountData entry (the wallet's token account), linked back only via a
nested userAccount field. tokenTransfers (Helius's own pre-aggregated,
already-decimal-adjusted summary) is a far more robust source for the
token side and is used here instead.
"""
WSOL_MINT = "So11111111111111111111111111111111111111112"
QUOTE_MINTS = {
    WSOL_MINT,
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
}
# ~0.01 SOL — below this, a negative native balance change on the wallet's
# own accountData entry is just network-fee noise, not a funding leg.
FEE_NOISE_LAMPORTS = 10_000_000


def parse_buy_event(tx: dict, tracked_wallet: str) -> dict | None:
    """
    One Helius enhanced-webhook transaction + the wallet being tracked ->
    a structured buy event, or None if this tx isn't a SOL/quote-funded
    buy for that wallet (a sell, a token-to-token swap, an error'd tx, or
    unrelated to this wallet).
    """
    if tx.get('type') != 'SWAP':
        return None
    if tx.get('transactionError'):
        return None

    # SOL side: the wallet's own accountData entry's nativeBalanceChange is
    # an ALL-IN figure — network fee, any platform/referral fees, and any
    # new-token-account rent are bundled in with the actual swap amount
    # (confirmed against the real payload: -4.032 SOL total wallet change
    # vs. 3.9506 SOL actually routed to the pool). Good enough to detect
    # "was this SOL-funded," not precise enough to call an exact swap price
    # — and the accumulator this feeds only needs the token side anyway.
    wallet_account = next(
        (a for a in tx.get('accountData', []) if a.get('account') == tracked_wallet),
        None
    )
    native_change = wallet_account.get('nativeBalanceChange', 0) if wallet_account else 0
    sol_spent_lamports = max(0, -native_change)
    funded_by_native_sol = sol_spent_lamports > FEE_NOISE_LAMPORTS

    transfers = tx.get('tokenTransfers', [])

    quote_spent = next(
        (t for t in transfers
         if t.get('fromUserAccount') == tracked_wallet and t.get('mint') in QUOTE_MINTS),
        None
    )
    if not funded_by_native_sol and quote_spent is None:
        return None  # nothing spent that looks like a funding leg

    # Largest non-quote token received by the wallet — mirrors the
    # "pick the biggest delta" approach needed because a multi-hop route
    # could touch intermediate tokens the wallet only passed through.
    bought = None
    for t in transfers:
        if t.get('toUserAccount') != tracked_wallet:
            continue
        if t.get('mint') in QUOTE_MINTS:
            continue
        amount = t.get('tokenAmount', 0)
        if amount > 0 and (bought is None or amount > bought['amount']):
            bought = {'mint': t['mint'], 'amount': amount}

    if bought is None:
        return None  # spent something but received no new token — not a buy we recognize

    return {
        'wallet': tracked_wallet,
        'token_mint': bought['mint'],
        'token_amount': bought['amount'],
        'sol_spent': sol_spent_lamports / 1e9 if funded_by_native_sol else None,
        'quote_spent': {'mint': quote_spent['mint'], 'amount': quote_spent['tokenAmount']} if quote_spent else None,
        'tx_signature': tx.get('signature'),
        'timestamp': tx.get('timestamp'),
        'source_dex': tx.get('source'),
    }


if __name__ == "__main__":
    import json

    print("[*] Verifying parse_buy_event against the real captured PUMP_FUN payload...")

    # The actual payload captured from a live Helius webhook (signature
    # 49aJJJXK...), trimmed to the fields this parser reads.
    real_tx = {
        "accountData": [
            {"account": "4bHUoZY36hmtW1FbJ1XJ5umWmFWXxx9voAe94M8PWNpL",
             "nativeBalanceChange": -4032044280, "tokenBalanceChanges": []},
            {"account": "6JxtrwtV3uB7PCrADNvZV3yTs4RbJ74CQNZHR9meScvT",
             "nativeBalanceChange": 3950617283, "tokenBalanceChanges": []},
            {"account": "7h4GMEuzdXpMLTAfr2AZvTZgAmyDxGj6ozMhQfnCyNys",
             "nativeBalanceChange": 2039280,
             "tokenBalanceChanges": [{
                 "mint": "DgWA3F1w6fzhf4jtphvBAcSqbK9iXHqmMRH6Sr1Gpump",
                 "rawTokenAmount": {"decimals": 6, "tokenAmount": "71334900280873"},
                 "tokenAccount": "7h4GMEuzdXpMLTAfr2AZvTZgAmyDxGj6ozMhQfnCyNys",
                 "userAccount": "4bHUoZY36hmtW1FbJ1XJ5umWmFWXxx9voAe94M8PWNpL"
             }]},
        ],
        "fee": 30005000,
        "feePayer": "4bHUoZY36hmtW1FbJ1XJ5umWmFWXxx9voAe94M8PWNpL",
        "signature": "49aJJJXKTXYueb8rT2xaiM9EPWKWSPUPXEk79ruUHBpFvkgu77w6a4tUz2uQ6XidH7tSj8haimzuDp54kQjWLkS8",
        "slot": 439916286,
        "source": "PUMP_FUN",
        "timestamp": 1786997480,
        "tokenTransfers": [{
            "fromTokenAccount": "DGNoQW1iqRmuSiQCPyx2KpQe5EAA6ZffPqSnyosjhJr4",
            "fromUserAccount": "6JxtrwtV3uB7PCrADNvZV3yTs4RbJ74CQNZHR9meScvT",
            "mint": "DgWA3F1w6fzhf4jtphvBAcSqbK9iXHqmMRH6Sr1Gpump",
            "toTokenAccount": "7h4GMEuzdXpMLTAfr2AZvTZgAmyDxGj6ozMhQfnCyNys",
            "toUserAccount": "4bHUoZY36hmtW1FbJ1XJ5umWmFWXxx9voAe94M8PWNpL",
            "tokenAmount": 71334900.280873,
            "tokenStandard": "UnknownStandard"
        }],
        "transactionError": None,
        "type": "SWAP",
    }

    result = parse_buy_event(real_tx, "4bHUoZY36hmtW1FbJ1XJ5umWmFWXxx9voAe94M8PWNpL")
    print(json.dumps(result, indent=2))

    assert result is not None, "FAILED to parse a real, confirmed buy transaction"
    assert result['token_mint'] == "DgWA3F1w6fzhf4jtphvBAcSqbK9iXHqmMRH6Sr1Gpump"
    assert abs(result['token_amount'] - 71334900.280873) < 1e-6
    assert result['sol_spent'] is not None and abs(result['sol_spent'] - 4.03204428) < 1e-6
    assert result['source_dex'] == "PUMP_FUN"
    assert result['tx_signature'] == "49aJJJXKTXYueb8rT2xaiM9EPWKWSPUPXEk79ruUHBpFvkgu77w6a4tUz2uQ6XidH7tSj8haimzuDp54kQjWLkS8"

    # A wallet NOT party to this transaction should get no result.
    assert parse_buy_event(real_tx, "SomeOtherWallet1111111111111111111111111") is None

    print("\n[OK] Parser correctly reconstructs the real transaction.")
