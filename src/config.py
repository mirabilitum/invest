import os
import yaml


def _load_config(path: str = "config.yaml") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _resolve_env(cfg: dict) -> dict:
    result = {}
    for k, v in cfg.items():
        if k.endswith("_env"):
            result[k[:-4]] = os.environ.get(v, "")
        elif isinstance(v, dict):
            result[k] = _resolve_env(v)
        else:
            result[k] = v
    return result


_CONFIG = _resolve_env(_load_config())


def get(path: str, default=None):
    keys = path.split(".")
    node = _CONFIG
    for k in keys:
        if isinstance(node, dict) and k in node:
            node = node[k]
        else:
            return default
    return node


DB_PATH = get("db.path", "data/invest.db")
