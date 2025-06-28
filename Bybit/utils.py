import base64
import os

BASE_DIRECTORY_PATH = os.path.dirname(os.path.abspath(__file__))
API_KEY_FILE_PATH = os.path.join(BASE_DIRECTORY_PATH, "API Keys\\api_key.txt")
API_SECRET_FILE_PATH = os.path.join(BASE_DIRECTORY_PATH, "API Keys\\api_secret.txt")

def read_api_key(testnet_mode: bool = True,local_running: bool = False) -> str:
    env_var = "BYBIT_API_KEY_TESTNET" if testnet_mode else "BYBIT_API_KEY"
    if env_var == "BYBIT_API_KEY_TESTNET": 
        if local_running:       
            with open(API_KEY_FILE_PATH, "rt") as api_file:
                content = api_file.read().strip()
                return content
        else:
            try:
                env_key = os.environ.get(env_var)
                return base64.b64decode(env_key).decode("utf-8")
            except Exception:
                return env_key
    else:
        try:
            env_key = os.environ.get(env_var)
            return base64.b64decode(env_key).decode("utf-8")
        except Exception:
            return env_key  # If not base64, return as is


def read_api_secret(testnet_mode: bool = True,local_running: bool = False) -> str:
    env_var = "BYBIT_API_SECRET_TESTNET" if testnet_mode else "BYBIT_API_SECRET"
    if env_var == "BYBIT_API_SECRET_TESTNET": 
        if local_running:       
            with open(API_SECRET_FILE_PATH, "rt") as api_file:
                content = api_file.read().strip()
                return content
        else:
            try:
                env_key = os.environ.get(env_var)
                return base64.b64decode(env_key).decode("utf-8")
            except Exception:
                return env_key
    else:
        try:
            env_key = os.environ.get(env_var)
            return base64.b64decode(env_key).decode("utf-8")
        except Exception:
            return env_key  # If not base64, return as is
