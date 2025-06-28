import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .constants import TARGET_HOURS_ISRAEL, WICK_PERCENTAGE_OF_BODY

RISK_REWARD_RATIO = 3.0
BYBIT_LEVERAGE_DECIMAL_LIMIT = 2
BYBIT_MAXIMUM_LEVERAGE_PERCENTAGE = 100
STOP_LOSS_TICKS = 2


def unix_milliseconds_to_timestamp(unix_milliseconds: int, timezone_string: str):
    unix_timestamp = unix_milliseconds / 1000

    utc_timestamp = datetime.fromtimestamp(unix_timestamp, tz=timezone.utc)

    return utc_timestamp.astimezone(ZoneInfo(timezone_string))


def is_candle_in_target_hours(candle_timestamp) -> bool:
    time_of_day = candle_timestamp.strftime("%H:%M:%S")

    return time_of_day in TARGET_HOURS_ISRAEL


def find_target_hour_candle(candles: list) -> dict:
    for candle in candles:
        if is_candle_in_target_hours(candle["start_time"]):
            return candle
    return {}


def is_candle_doji(candle: dict) -> bool:
    open_price = float(candle["open"])
    close_price = float(candle["close"])
    high = float(candle["high"])
    low = float(candle["low"])

    body = abs(open_price - close_price)
    upper_wick = high - max(open_price, close_price)
    lower_wick = min(open_price, close_price) - low

    # Make sure the candle isn't flat.
    is_flat = 0 == body and 0 == upper_wick and 0 == lower_wick

    return (
            upper_wick >= WICK_PERCENTAGE_OF_BODY * body and
            lower_wick >= WICK_PERCENTAGE_OF_BODY * body and
            upper_wick > 0 and
            lower_wick > 0 and
            not is_flat
    )


def calculate_long_order_data(candle: dict, tick_size: float) -> dict:
    # The result matches the Bybit API.
    entry = candle["high"]
    stop_loss = candle["low"] - (tick_size * STOP_LOSS_TICKS)
    take_profit = entry + RISK_REWARD_RATIO * (entry - stop_loss)

    assert entry != stop_loss, f"Bad candle, entry == stop_loss. Candle: {candle}"
    assert stop_loss != take_profit, f"Bad candle, stop_loss == take_profit. Candle: {candle}"

    return {
        "Side": "Buy",
        "Entry": entry,
        "StopLoss": stop_loss,
        "TakeProfit": take_profit
    }


def calculate_short_order_data(candle: dict, tick_size: float) -> dict:
    # The result matches the Bybit API.
    entry = candle["low"]
    stop_loss = candle["high"] + (tick_size * STOP_LOSS_TICKS)
    take_profit = entry - RISK_REWARD_RATIO * (stop_loss - entry)

    assert entry != stop_loss, f"Bad candle, entry == stop_loss. Candle: {candle}"
    assert stop_loss != take_profit, f"Bad candle, stop_loss == take_profit. Candle: {candle}"

    return {
        "Side": "Sell",
        "Entry": entry,
        "StopLoss": stop_loss,
        "TakeProfit": take_profit
    }


def calculate_order_leverage(entry_price: float, stop_loss_price: float, maximum_loss_percentage: float) -> float:
    """
    This function returns by how much you should multiply your trade in order for a stop-loss percentage move to
    be like `maximum_loss_percentage`. i.e., If the stop-loss is 5% away from the entry (calculated relative to
    the entry), and you want it to be 10%, then you'll get a leverage value of 2.
    """
    if entry_price == stop_loss_price:
        logging.error(f"Entry price and stop loss price cannot be the same. Value: {entry_price}")
        raise ValueError(f"Entry price and stop loss price cannot be the same. Value: {entry_price}")

    # Price drop (in percentages) relative to the entry price.
    relative_loss = abs(entry_price - stop_loss_price) / entry_price

    leverage = maximum_loss_percentage / relative_loss
    leverage = round(leverage, BYBIT_LEVERAGE_DECIMAL_LIMIT)

    if leverage > BYBIT_MAXIMUM_LEVERAGE_PERCENTAGE:
        logging.info(
            f"Calculated leverage ({leverage}) exceeds the maximum allowed ({BYBIT_MAXIMUM_LEVERAGE_PERCENTAGE}). "
            f"Returning maximum allowed instead of calculated."
        )
        return BYBIT_MAXIMUM_LEVERAGE_PERCENTAGE

    return leverage


def calculate_order_quantity(entry_price: float, total_money_for_trade: float, leverage: float) -> float:
    position_value = total_money_for_trade * leverage

    return position_value / entry_price
