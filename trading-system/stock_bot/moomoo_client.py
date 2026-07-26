"""Moomoo/OpenD integration surface for stock execution.

All Moomoo SDK and OpenD-specific code is confined to this module so
stock_bot/execution.py stays broker-agnostic.

Safety: trading environment is hardcoded to TrdEnv.SIMULATE below and is
not read from any env var or accepted as a function parameter. Reaching
TrdEnv.REAL requires an explicit, separate edit to this file — it can't
be flipped via .env or by any caller.
"""
import os
from dotenv import load_dotenv
from moomoo import (
    OpenSecTradeContext,
    TrdMarket,
    TrdEnv,
    TrdSide,
    OrderType,
    SecurityFirm,
    RET_OK,
)

load_dotenv()

_TRD_ENV = TrdEnv.SIMULATE  # hardcoded — see module docstring


def _opend_host_port():
    return (
        os.getenv('MOOMOO_OPEND_HOST', '127.0.0.1'),
        int(os.getenv('MOOMOO_OPEND_PORT', 11111)),
    )


def _account_id() -> int:
    return int(os.getenv('MOOMOO_ACCOUNT_ID'))


def get_moomoo_context():
    """Open a trade context against OpenD. Caller owns its lifecycle (close() when done)."""
    host, port = _opend_host_port()
    return OpenSecTradeContext(
        filter_trdmarket=TrdMarket.US,
        host=host,
        port=port,
        security_firm=SecurityFirm.FUTUSECURITIES,
    )


def unlock_trade(ctx) -> dict:
    """Trade-unlock handshake.

    Verified against a live OpenD SIMULATE account: this always fails with
    "Missing necessary parameter" for markets (like US) that support real
    trading, because the SDK gates on whether the *market* can ever go REAL,
    not on which trd_env a given order uses — so it demands a password
    unconditionally, even for SIMULATE-only usage. No password is read or
    stored here (deliberately — see .env comments), so this call is expected
    to fail and its result is informational only.

    Confirmed place_order under TrdEnv.SIMULATE succeeds without this ever
    succeeding, so nothing on the SIMULATE path depends on its result.
    """
    ret, data = ctx.unlock_trade(password=None, is_unlock=True)
    return {'ret_ok': ret == RET_OK, 'data': data}


def place_order(ctx, ticker: str, direction: str, quantity: float, price: float) -> dict:
    """Submit an order via OpenD, hardcoded to TrdEnv.SIMULATE."""
    if direction != 'BUY_SIGNAL':
        raise ValueError(f"Unsupported direction for order placement: {direction!r}")

    code = f"US.{ticker}"
    ret, data = ctx.place_order(
        price=price,
        qty=quantity,
        code=code,
        trd_side=TrdSide.BUY,
        order_type=OrderType.NORMAL,
        trd_env=_TRD_ENV,
        acc_id=_account_id(),
    )

    if ret != RET_OK or data.empty:
        return {'ret_ok': False, 'order_id': None, 'raw': data}

    row = data.iloc[0]
    return {
        'ret_ok': True,
        'order_id': row['order_id'],
        'code': row['code'],
        'order_status': row['order_status'],
        'price': row['price'],
        'qty': row['qty'],
    }


def get_order_status(ctx, order_id: str) -> dict:
    """Poll an order's fill/status from OpenD, hardcoded to TrdEnv.SIMULATE."""
    ret, data = ctx.order_list_query(
        order_id=order_id,
        trd_env=_TRD_ENV,
        acc_id=_account_id(),
    )

    if ret != RET_OK or data.empty:
        return {'ret_ok': False, 'raw': data}

    row = data.iloc[0]
    return {
        'ret_ok': True,
        'order_id': row['order_id'],
        'code': row['code'],
        'order_status': row['order_status'],
        'price': row['price'],
        'qty': row['qty'],
        'dealt_qty': row['dealt_qty'],
        'dealt_avg_price': row['dealt_avg_price'],
        'create_time': row['create_time'],
    }
