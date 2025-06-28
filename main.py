"""
Strategy:
3-minute btc-usd prices.
Find Doji candles at specific hours.
Short on the bottom of the wick, stop loss a bit above the top wick.
Simultaneously, long on the top of the wick, stop loss a bit below the bottom of the wick.
Whichever order enters first, cancel the other one.
"""
import logging
import os
import sys

from Bybit.bot import start_bot

DAYS_TO_RUN = 2
#You can update Mannualy a IS_BYBIT_TESTNET_MODE for True or False.
#If you want to run the bot in testnet mode, 
#   set the environment variable BYBIT_MODE to "testnet" in server or here locally IS_BYBIT_TESTNET_MODE = True.

IS_BYBIT_TESTNET_MODE = True if os.environ.get("BYBIT_MODE", "testnet") == "testnet" else False
IS_BYBIT_LOCAL_RUNNING = False if os.environ.get("BYBIT_MODE") else True

def setup_logger():
    log_format = '%(asctime)s - %(levelname)s - %(message)s'
    logging.basicConfig(
        filename='bot.log',
        filemode='a',
        format=log_format,
        # Setting to INFO and not DEBUG because the pybit package itself prints a lot of stuff in DEBUG mode.
        level=logging.INFO
    )
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)

    console_handler.setFormatter(logging.Formatter(log_format))

    logging.getLogger().addHandler(console_handler)


def main():
    setup_logger()
    start_bot(DAYS_TO_RUN, IS_BYBIT_TESTNET_MODE, IS_BYBIT_LOCAL_RUNNING)


if __name__ == "__main__":
    main()
