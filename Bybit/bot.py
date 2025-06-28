import os
import threading
import time
import logging
import traceback
import signal
from datetime import datetime, timedelta

from pybit.exceptions import InvalidRequestError
from pybit.unified_trading import HTTP

from Strategy.constants import TARGET_HOURS_ISRAEL, RISK_PER_POSITION_PERCENTAGE
from Strategy.live_strategy import is_candle_doji, find_target_hour_candle, calculate_long_order_data, \
    calculate_short_order_data, calculate_order_leverage, unix_milliseconds_to_timestamp, calculate_order_quantity

from .thread_safe_session import ThreadSafeSession
from .utils import read_api_key, read_api_secret


SYMBOL_TO_TRADE = "BTCUSDT"
PRODUCT_TYPE = "linear"
ACCOUNT_CURRENCY = "USDT"
ACCOUNT_TYPE = "UNIFIED"
ORDER_TYPE = "Market"
CHART_INTERVAL = 3
# Bybit limits 600 requests per IP per 5 seconds.
POLL_ORDER_FILL_SECONDS = 0.5
ISOLATED_TRADE_MODE = 1
LEVERAGE_NOT_MODIFIED_ERROR_CODE = 110043
ROUNDING_PRECISION = 6
SECONDS_IN_MINUTE = 60
SHOULD_USE_LEVERAGE = 1  # True
ONE_WAY_MODE_POSITION_INDEX = 0
CANDLES_TO_GET = 3
POLLING_LOG_TIME_SECONDS = 30
MARGIN_MODE = "ISOLATED_MARGIN"


def sleep_until_next_target_hour() -> None:
    now = datetime.now()
    today = now.date()

    # Parse hours into datetime objects today.
    target_datetimes = [
        datetime.strptime(f"{today} {hour}", "%Y-%m-%d %H:%M:%S") for hour in TARGET_HOURS_ISRAEL
    ]

    # Find the next target time that is still ahead (may also be the current candle).
    for timestamp in reversed(target_datetimes):
        if now - timestamp < timedelta(minutes=3) and timestamp <= now:
            next_target = timestamp
            break
    else:
        next_target = next((timestamp for timestamp in target_datetimes if timestamp >= now), None)

    # If there aren't any future times today, wait until the first time tomorrow.
    if not next_target:
        next_day = today + timedelta(days=1)
        next_target = datetime.strptime(f"{next_day} {TARGET_HOURS_ISRAEL[0]}", "%Y-%m-%d %H:%M:%S")

    # Adding 3 minutes because we want to wait for the candle at the specified hour to close.
    # Adding a buffer because if we're too fast, the latest candle won't be received in the API response made later.
    next_target += timedelta(minutes=3.08)

    seconds_to_sleep = (next_target - now).total_seconds()

    logging.info(f"Sleeping until next target: {next_target} (in {seconds_to_sleep / SECONDS_IN_MINUTE:.1f} minutes)")

    time.sleep(seconds_to_sleep)

    logging.info(f"Woken up from sleep. Current time: {datetime.now()}")


def get_latest_candles(api: ThreadSafeSession) -> list:
    response = api.get_kline(
        category=PRODUCT_TYPE, symbol=SYMBOL_TO_TRADE, interval=CHART_INTERVAL, limit=CANDLES_TO_GET
    )

    candles = response["result"]["list"]

    if not candles:
        logging.error("Failed to get candles from API.")
        raise RuntimeError("Failed to get candles from API.")

    # Index zero is the currently forming candle (it hasn't closed yet).
    return candles[1:]


def get_wallet_balance(api: ThreadSafeSession) -> float:
    response = api.get_wallet_balance(accountType=ACCOUNT_TYPE, coin=ACCOUNT_CURRENCY)

    return float(response["result"]["list"][0]["totalWalletBalance"])


def get_exchange_information(api: ThreadSafeSession) -> dict:
    response = api.get_instruments_info(category=PRODUCT_TYPE, symbol=SYMBOL_TO_TRADE)

    return response["result"]["list"][0]


def place_order(api: ThreadSafeSession, order: dict) -> str:
    # 1: If market price rises to trigger price. 2: If market price falls to trigger price.
    trigger_direction = 1 if "Buy" == order['Side'] else 2

    logging.info(f'''Placing order:
        category={PRODUCT_TYPE},
        symbol={SYMBOL_TO_TRADE},
        isLeverage={SHOULD_USE_LEVERAGE},
        side="{order['Side']}",
        orderType={ORDER_TYPE},
        qty="{order['Quantity']}",
        price="{order['Entry']}",
        triggerDirection={trigger_direction},
        triggerPrice="{order['Entry']}",
        triggerBy="LastPrice",
        timeInForce="GTC",
        positionIdx={ONE_WAY_MODE_POSITION_INDEX},
        takeProfit="{order['TakeProfit']}",
        stopLoss="{order['StopLoss']}",
        tpTriggerBy="LastPrice",
        slTriggerBy="LastPrice",
        reduceOnly=False,
        closeOnTrigger=False,
        tpslMode="Full",
        tpOrderType="Market",
        slOrderType="Market"
    ''')

    try:
        logging.info(f"Setting buy leverage and sell leverage to: {order['Leverage']}%")
        api.set_leverage(
            category=PRODUCT_TYPE,
            symbol=SYMBOL_TO_TRADE,
            buyLeverage=str(order["Leverage"]),
            sellLeverage=str(order["Leverage"])
        )
    except InvalidRequestError as e:
        if LEVERAGE_NOT_MODIFIED_ERROR_CODE != e.status_code:
            raise e

    response = api.place_order(
        category=PRODUCT_TYPE,
        symbol=SYMBOL_TO_TRADE,
        isLeverage=SHOULD_USE_LEVERAGE,
        side=order["Side"],
        orderType=ORDER_TYPE,
        qty=str(order['Quantity']),
        price=str(order["Entry"]),
        triggerDirection=trigger_direction,
        triggerPrice=str(order["Entry"]),
        triggerBy="LastPrice",
        timeInForce="GTC",
        positionIdx=ONE_WAY_MODE_POSITION_INDEX,
        takeProfit=str(order["TakeProfit"]),
        stopLoss=str(order["StopLoss"]),
        tpTriggerBy="LastPrice",
        slTriggerBy="LastPrice",
        reduceOnly=False,
        closeOnTrigger=False,
        tpslMode="Full",
        tpOrderType="Market",
        slOrderType="Market"
    )

    return response["result"]["orderId"]


def round_to_bybit_requirements(value: float, step: float) -> float:
    precision = len(str(step).split(".")[1])
    return round(round(value / step) * step, ndigits=precision)


def conform_leverage_to_bybit(desired_leverage: float, leverage_filter: dict) -> float:
    maximum_leverage = float(leverage_filter["maxLeverage"])
    if desired_leverage > maximum_leverage:
        logging.warning(
            f"Desired trade leverage ({desired_leverage}%) exceeds the maximum allowed ({maximum_leverage}%). "
            f"Setting trade leverage to maximum allowed."
        )
        desired_leverage = maximum_leverage

    minimum_leverage = float(leverage_filter["minLeverage"])
    if desired_leverage < minimum_leverage:
        logging.warning(
            f"Desired trade leverage ({desired_leverage}%) is below the minimum allowed ({maximum_leverage}%)."
        )
        raise RuntimeError(
            f"Desired trade leverage ({desired_leverage}%) is below the minimum allowed ({maximum_leverage}%)."
        )

    return desired_leverage


def conform_quantity_to_bybit(desired_quantity: float, lot_size_filter: dict) -> float:
    quantity_step = float(lot_size_filter["qtyStep"])

    valid_quantity = round_to_bybit_requirements(desired_quantity, quantity_step)

    logging.info(f"Rounding order quantity from {desired_quantity} to {valid_quantity}")

    maximum_quantity = float(lot_size_filter.get("maxOrderQty", float("inf")))
    minimum_quantity = float(lot_size_filter["minOrderQty"])

    if valid_quantity < minimum_quantity:
        logging.warning(
            f"Desired trade quantity ({valid_quantity}) is smaller than the allowed quantity ({minimum_quantity})."
        )
        raise RuntimeError(
            f"Desired trade quantity ({valid_quantity}) is smaller than the allowed quantity ({minimum_quantity})."
        )
    elif valid_quantity > maximum_quantity:
        logging.warning(
            f"Desired trade quantity ({valid_quantity}) exceeds the allowed quantity ({minimum_quantity})."
        )
        raise RuntimeError(
            f"Desired trade quantity ({valid_quantity}) exceeds than the allowed quantity ({minimum_quantity})."
        )

    return valid_quantity


def validate_order_prices_after_conformation(order: dict, candle: dict, tick_size: float) -> dict:
    # Make sure we didn't screw up the strategy. We don't check the take-profit because it's not as critical.
    if "Buy" == order["Side"]:
        if order["Entry"] < candle["high"]:
            logging.warning(f"Rounding of LONG entry caused it to be below candle high. Adding one tick.")
            order["Entry"] += tick_size
        if order["StopLoss"] >= candle["low"]:
            logging.warning(
                f"Rounding of LONG stop-loss caused it to be above or equal to candle low. Subtracting one tick."
            )
            order["StopLoss"] -= tick_size

    if "Sell" == order["Side"]:
        if order["Entry"] > candle["low"]:
            logging.warning(f"Rounding of SHORT entry caused it to be above candle low. Subtracting one tick.")
            order["Entry"] -= tick_size
        if order["StopLoss"] <= candle["high"]:
            logging.warning(
                f"Rounding of SHORT stop-loss caused it to be below or equal to candle high. Adding one tick."
            )
            order["StopLoss"] += tick_size

    return order


def validate_position_can_be_opened(order: dict, wallet_size: float):
    required_money = (order["Quantity"] * order["Entry"]) / order["Leverage"]
    # Use greater-equals instead greater-than due to rounding.
    if round(required_money, ROUNDING_PRECISION) >= round(wallet_size, ROUNDING_PRECISION):
        logging.error(
            f"Required money for trade (${required_money}) exceeds the money available for trading (${wallet_size}). "
            f"Order: {order}"
        )
        raise RuntimeError(
            f"Required money for trade (${required_money}) exceeds the money available for trading (${wallet_size}). "
            f"Order: {order}"
        )


def conform_order_to_bybit(order: dict, candle: dict, exchange_information: dict, wallet_size: float) -> dict:
    order["Leverage"] = conform_leverage_to_bybit(order["Leverage"], exchange_information["leverageFilter"])

    order["Quantity"] = conform_quantity_to_bybit(order["Quantity"], exchange_information["lotSizeFilter"])

    price_filter = exchange_information["priceFilter"]

    tick_size = float(price_filter["tickSize"])
    for field in ["Entry", "StopLoss", "TakeProfit"]:
        rounded_value = round_to_bybit_requirements(order[field], tick_size)

        logging.info(f"Rounding order['{field}'] from {order[field]} to {rounded_value}")

        order[field] = rounded_value

    order = validate_order_prices_after_conformation(order, candle, tick_size)

    validate_position_can_be_opened(order, wallet_size)

    return order


def was_order_filled(api: ThreadSafeSession, order_id: str) -> bool:
    """
    This function also returns true for partially filled orders!
    """
    response = api.get_open_orders(category=PRODUCT_TYPE, orderId=order_id)

    if not response["result"]["list"]:
        logging.error("get_open_orders API failed. Invalid order ID?")
        raise RuntimeError("get_open_orders API failed. Invalid order ID?")

    # Using index zero because we query directly by order ID (we shouldn't receive all the other open orders)
    order_status = response["result"]["list"][0]["orderStatus"]

    if "Filled" == order_status or "PartiallyFilled" == order_status:
        logging.info(f"Considering order {order_id} as filled. Real Status: {order_status}")
        return True

    if "New" == order_status or "Untriggered" == order_status:
        return False

    logging.error(f"Unexpected order status: {order_status}")
    raise RuntimeError(f"Unexpected order status: {order_status}")


def cancel_order(api: ThreadSafeSession, order_id: str) -> None:
    # This can happen if an order was already filled, and we attempt to cancel it.
    # Because we use "one-way" account, bybit allows us to only have a long or short per symbol (but not both).
    try:
        api.cancel_order(category=PRODUCT_TYPE, symbol=SYMBOL_TO_TRADE, orderId=order_id)
        logging.info(f"Successfully closed order {order_id}")
    except Exception as e:
        logging.warning(f"Failed to cancel order {order_id}. Error: {e}")


def wait_for_orders(api: ThreadSafeSession, long_order_id: str, short_order_id: str) -> None:
    last_log_time = 0

    # Wait until one order is filled, then cancel the other one.
    while True:
        current_time = time.time()

        if current_time - last_log_time >= POLLING_LOG_TIME_SECONDS:
            logging.info(
                f"Waiting for orders to be filled. Long order ID: {long_order_id}, Short order ID: {short_order_id}"
            )
            last_log_time = current_time

        time.sleep(POLL_ORDER_FILL_SECONDS)

        long_order_filled = was_order_filled(api, long_order_id)
        short_order_filled = was_order_filled(api, short_order_id)

        if long_order_filled and short_order_filled:
            logging.info("Both long and short orders were filled for the same trade. Closing both positions.")
            cancel_order(api, long_order_id)
            cancel_order(api, short_order_id)

        if long_order_filled:
            logging.info("Closing short order.")
            cancel_order(api, short_order_id)
            break
        elif short_order_filled:
            logging.info("Closing long order.")
            cancel_order(api, long_order_id)
            break


def run_bot(api: ThreadSafeSession, days_to_run: int) -> None:
    start_time = datetime.now()
    end_time = start_time + timedelta(days=days_to_run)

    while datetime.now() < end_time:
        sleep_until_next_target_hour()

        latest_candles = get_latest_candles(api)

        candles_data = [
            {
                "start_time": unix_milliseconds_to_timestamp(int(candle[0]), "Asia/Jerusalem"),
                "open": float(candle[1]),
                "high": float(candle[2]),
                "low": float(candle[3]),
                "close": float(candle[4]),
                "volume": float(candle[5])
            } for candle in latest_candles
        ]

        candle_data = find_target_hour_candle(candles_data)
        if not candle_data:
            logging.error(
                f"Failed to find candle in target hour. Current time: {datetime.now()}, Candles: {candles_data}"
            )
            raise RuntimeError(
                f"Failed to find candle in target hour. Current time: {datetime.now()}, Candles: {candles_data}"
            )
        
        if not is_candle_doji(candle_data):
            logging.info(f"Candle is not a doji. Candle: {candle_data}")
            continue

        logging.info(f"Identified doji candle: {candle_data}")

        exchange_information = get_exchange_information(api)

        tick_size = float(exchange_information["priceFilter"]["tickSize"])

        long_order = calculate_long_order_data(candle_data, tick_size)
        short_order = calculate_short_order_data(candle_data, tick_size)

        long_order["Leverage"] = calculate_order_leverage(
            long_order["Entry"], long_order["StopLoss"], RISK_PER_POSITION_PERCENTAGE
        )
        short_order["Leverage"] = calculate_order_leverage(
            short_order["Entry"], short_order["StopLoss"], RISK_PER_POSITION_PERCENTAGE
        )

        # Using wallet balance and not account balance to be able to have multiple open positions at a time.
        wallet_balance = get_wallet_balance(api)

        long_order["Quantity"] = calculate_order_quantity(
            long_order["Entry"], wallet_balance // 2, long_order["Leverage"]
        )

        short_order["Quantity"] = calculate_order_quantity(
            short_order["Entry"], wallet_balance // 2, short_order["Leverage"]
        )

        long_order = conform_order_to_bybit(long_order, candle_data, exchange_information, wallet_balance)
        short_order = conform_order_to_bybit(short_order, candle_data, exchange_information, wallet_balance)

        long_order_id = place_order(api, long_order)
        short_order_id = place_order(api, short_order)

        threading.Thread(target=wait_for_orders, args=(api, long_order_id, short_order_id), daemon=True).start()


def cancel_non_important_orders(api: ThreadSafeSession):
    """
    Cancel all orders which aren't stop-loss or take-profit orders.
    """
    open_orders = api.get_open_orders(category=PRODUCT_TYPE, symbol=SYMBOL_TO_TRADE)["result"]["list"]

    for order in open_orders:
        order_id = order.get("orderId")
        order_filter = order.get("orderFilter")
        reduce_only = order.get("reduceOnly", False)
        close_on_trigger = order.get("closeOnTrigger", False)

        if (
                order.get("orderFilter") == "tpslOrder" or
                order.get("reduceOnly", False) or
                order.get("closeOnTrigger", False)
        ):
            logging.info(
                f"Keeping TP/SL order: {order_id} "
                f"(filter={order_filter}, reduceOnly={reduce_only}, closeOnTrigger={close_on_trigger})"
            )
            continue

        # Suppressing default pybit exception throwing because we want a best-effort cleanup.
        try:
            api.cancel_order(category=PRODUCT_TYPE, symbol=SYMBOL_TO_TRADE, orderId=order_id)
            logging.info(f"Canceled non-TP/SL order: {order_id} (price={order.get('price')}, side={order.get('side')})")
        except Exception as e:
            logging.error(f"Failed to cancel order {order_id}: {e}")


def print_remaining_open_orders(api: ThreadSafeSession):
    response = api.get_open_orders(category=PRODUCT_TYPE, symbol=SYMBOL_TO_TRADE)
    logging.info("Printing currently open orders.")
    for order in response["result"]["list"]:
        logging.info(
            f"ID: {order.get('orderId')}, Side: {order.get('side')}, Price: {order.get('price')}"
        )


def print_open_positions(api: ThreadSafeSession):
    positions = api.get_positions(category=PRODUCT_TYPE, symbol=SYMBOL_TO_TRADE)["result"]["list"]

    logging.info("Printing currently open positions.")

    if not positions:
        logging.info("No open positions found.")
        return

    for pos in positions:
        size = float(pos.get("size", 0))
        side = pos.get("side")
        entry_price = pos.get("entryPrice")
        leverage = pos.get("leverage")
        take_profit = pos.get("takeProfit")
        stop_loss = pos.get("stopLoss")
        unrealized_pnl = pos.get("unrealisedPnl")

        logging.info(
            f"Side: {side}, Quantity: {size}, Entry: {entry_price}, Leverage: {leverage}, "
            f"TP: {take_profit}, SL: {stop_loss}, PnL: {unrealized_pnl}"
        )


def cleanup(api: ThreadSafeSession):
    cancel_non_important_orders(api)
    print_remaining_open_orders(api)
    print_open_positions(api)


def exit_hook(api: ThreadSafeSession):
    def handle_exit(signum, frame):
        logging.info("Graceful shutdown signal received. Cleaning up.")
        cleanup(api)
        exit(0)

    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)


def start_bot(days_to_run,is_testnet_mode=True,is_local_running=False) -> None:
    session = HTTP(
        testnet=is_testnet_mode,
        api_key= 'lM8R3xocZWBDCUm3ZD',#read_api_key(is_testnet_mode,is_local_running),
        api_secret='nKfqVMMpRXcVMKJEP5WPHEnxcWwN52A3L7Mc' #read_api_secret(is_testnet_mode,is_local_running)
    )

    api = ThreadSafeSession(session)

    exit_hook(api)

    # This API should be called once per symbol. I think it throws when you call it multiple times on the same symbol.
    api.set_margin_mode(
        setMarginMode=MARGIN_MODE
    )

    try:
        run_bot(api, days_to_run)
    except Exception as e:
        logging.error(f"[ERROR] forward_test(): {e} | Traceback: {traceback.print_exc()}")

    cleanup(api)
