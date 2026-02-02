#!/usr/bin/env python3
import datetime
import json
import os
import signal
import sys
import threading
import time
import tomllib
from http.server import BaseHTTPRequestHandler, HTTPServer

DEFAULT_TEMP_ON_C = 55.0
DEFAULT_TEMP_OFF_C = 45.0


STATUS_LOCK = threading.Lock()
STATUS: dict = {}
MAX_SWITCH_HISTORY = 4
TZ_LOCAL = datetime.datetime.now().astimezone().tzinfo


def log(msg: str) -> None:
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    print(f"[{ts}] {msg}")


def warn(msg: str) -> None:
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    line = f"[{ts}] WARN: {msg}"
    print(line)
    print(line, file=sys.stderr)


def format_context(cfg: dict, temp_c: float | None) -> str:
    if temp_c is None:
        temp = "temp=unknown"
    else:
        temp = f"temp={temp_c:.1f}C"
    return f"{temp} on={cfg['temp_on_c']:.1f}C off={cfg['temp_off_c']:.1f}C"


def read_version() -> str:
    # Try package metadata first if installed, or local file
    try:
        from importlib.metadata import version
        return version("fanctrl")
    except Exception:
        pass
        
    candidates = [
        os.path.join(os.path.dirname(__file__), "../../VERSION"), # Local dev
        "VERSION",
    ]
    for path in candidates:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read().strip()
    return "unknown"


def update_status(payload: dict) -> None:
    with STATUS_LOCK:
        STATUS.clear()
        STATUS.update(payload)


def status_snapshot() -> dict:
    with STATUS_LOCK:
        return dict(STATUS)


def now_iso() -> str:
    return datetime.datetime.now(TZ_LOCAL).isoformat(timespec="seconds")


def append_switch_event(history: list[dict], *, fan_on: bool, temp_c: float | None, cfg: dict) -> None:
    history.append(
        {
            "ts": now_iso(),
            "fan_on": fan_on,
            "temp_c": None if temp_c is None else round(float(temp_c), 1),
            "temp_on_c": cfg["temp_on_c"],
            "temp_off_c": cfg["temp_off_c"],
        }
    )
    if len(history) > MAX_SWITCH_HISTORY:
        del history[0:len(history) - MAX_SWITCH_HISTORY]


class StatusHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path != "/status":
            self.send_response(404)
            self.end_headers()
            return
        payload = status_snapshot()
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # noqa: N802
        return


def run_status_server(port: int) -> None:
    server = HTTPServer(("0.0.0.0", port), StatusHandler)
    server.serve_forever()


def load_config(path: str) -> dict:
    if os.path.exists(path):
        with open(path, "rb") as f:
            data = tomllib.load(f)
        cfg = data.get("fan", {})
    else:
        warn(f"Config file {path} not found, using defaults")
        cfg = {}

    return {
        "gpio_pin": int(cfg.get("gpio_pin", 33)),
        # pin_mode is ignored/deprecated as gpiod uses chip lines directly
        "pin_mode": "BCM", 
        "active_high": bool(cfg.get("active_high", True)),
        "gpio_backend": "gpiod",
        "gpio_chip": str(cfg.get("gpio_chip", "gpiochip0")),
        "poll_interval_s": float(cfg.get("poll_interval_s", 5.0)),
        "temp_on_c": float(cfg.get("temp_on_c", DEFAULT_TEMP_ON_C)),
        "temp_off_c": float(cfg.get("temp_off_c", DEFAULT_TEMP_OFF_C)),
        "min_switch_s": float(cfg.get("min_switch_s", 10.0)),
        "temp_path": str(cfg.get("temp_path", "/sys/class/thermal/thermal_zone0/temp")),
        "log_each_read": bool(cfg.get("log_each_read", True)),
    }


def read_temp_c(path: str) -> float:
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read().strip()
    val = float(raw)
    return val / 1000.0 if val > 1000 else val


def set_fan_state(ctx: dict, active_high: bool, on: bool) -> None:
    level = 1 if (on == active_high) else 0
    ctx["line"].set_value(level)


def setup_gpio(cfg: dict):
    # Only gpiod is supported
    try:
        import gpiod
    except ImportError as exc:
        raise RuntimeError(f"gpiod import failed: {exc}. Install the 'gpiod' python package or system library.") from exc

    chip_path = f"/dev/{cfg['gpio_chip']}"
    if not os.path.exists(chip_path) and not os.environ.get("FANCTRL_MOCK_GPIO"):
        raise FileNotFoundError(f"GPIO chip not found: {chip_path}")
    
    # Allow mock for non-RPi testing
    if os.environ.get("FANCTRL_MOCK_GPIO"):
        log("WARN: Running with MOCK GPIO")
        return {"backend": "mock", "line": type("MockLine", (), {"set_value": lambda s, v: None, "release": lambda s: None})(), "chip": type("MockChip", (), {"close": lambda s: None})()}, cfg["gpio_pin"]

    if (
        hasattr(gpiod, "request_lines")
        and hasattr(gpiod, "LineSettings")
        and hasattr(gpiod, "LineDirection")
    ):
        default_off = 0 if cfg["active_high"] else 1
        try:
            value_enum = (
                gpiod.LineValue.ACTIVE if default_off == 1 else gpiod.LineValue.INACTIVE
            )
        except Exception:
            value_enum = default_off

        settings = gpiod.LineSettings(
            direction=gpiod.LineDirection.OUTPUT,
            output_value=value_enum,
        )

        request = gpiod.request_lines(
            chip_path,
            consumer="fanctrl",
            config={cfg["gpio_pin"]: settings},
        )

        class V2Line:
            def __init__(self, req, offset, gpiod_mod):
                self._req = req
                self._offset = offset
                self._gpiod = gpiod_mod

            def set_value(self, value):
                try:
                    val = (
                        self._gpiod.LineValue.ACTIVE
                        if value
                        else self._gpiod.LineValue.INACTIVE
                    )
                except Exception:
                    val = value
                self._req.set_value(self._offset, val)

            def release(self):
                self._req.release()

        return {
            "backend": "gpiod-v2",
            "line": V2Line(request, cfg["gpio_pin"], gpiod),
        }, cfg["gpio_pin"]

    chip = None
    try:
        chip = gpiod.Chip(cfg["gpio_chip"])
    except FileNotFoundError:
        chip = gpiod.Chip(chip_path)

    try:
        line = chip.get_line(cfg["gpio_pin"])
    except Exception as exc:
        raise RuntimeError(
            f"GPIO line request failed: chip={cfg['gpio_chip']} pin={cfg['gpio_pin']} (path={chip_path}): {exc}"
        ) from exc
    default_off = 0 if cfg["active_high"] else 1
    line.request(consumer="fanctrl", type=gpiod.LINE_REQ_DIR_OUT, default_vals=[default_off])
    return {"backend": "gpiod", "chip": chip, "line": line, "pin": cfg["gpio_pin"]}, cfg["gpio_pin"]


def cleanup_gpio(ctx: dict) -> None:
    if ctx.get("backend") in {"gpiod", "gpiod-v2"}:
        if "line" in ctx:
            ctx["line"].release()
        if "chip" in ctx:
            ctx["chip"].close()


def main() -> int:
    config_path = os.environ.get("FANCTRL_CONFIG", "/config/config.toml")
    status_port = int(os.environ.get("FANCTRL_STATUS_PORT", "9101"))
    
    # CLI arg support for config path could be added here, 
    # but for now we stick to env var or default path to match previous behavior
    if len(sys.argv) > 1 and sys.argv[1].endswith(".toml"):
         config_path = sys.argv[1]

    cfg = load_config(config_path)

    if cfg["temp_off_c"] > cfg["temp_on_c"] or (cfg["temp_on_c"] - cfg["temp_off_c"]) < 1.0:
        warn(
            "Invalid temperature thresholds in config; "
            f"temp_on_c={cfg['temp_on_c']}, temp_off_c={cfg['temp_off_c']}. "
            f"Falling back to defaults on={DEFAULT_TEMP_ON_C}, off={DEFAULT_TEMP_OFF_C}."
        )
        cfg["temp_on_c"] = DEFAULT_TEMP_ON_C
        cfg["temp_off_c"] = DEFAULT_TEMP_OFF_C

    if cfg["poll_interval_s"] <= 0:
        log("ERROR: poll_interval_s must be > 0")
        return 2
    if cfg["min_switch_s"] < 0:
        log("ERROR: min_switch_s must be >= 0")
        return 2

    try:
        gpio_ctx, bcm_pin = setup_gpio(cfg)
    except Exception as exc:
        log(f"ERROR: GPIO setup failed: {exc}")
        return 2

    state_on = False
    last_switch = 0.0
    min_temp = None
    max_temp = None
    switch_history: list[dict] = []

    def shutdown(signum, _frame):
        log(f"Signal {signum} received, shutting down")
        try:
            set_fan_state(gpio_ctx, cfg["active_high"], False)
        finally:
            cleanup_gpio(gpio_ctx)
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    log(f"Version {read_version()}")
    try:
        start_temp = read_temp_c(cfg["temp_path"])
    except Exception:
        start_temp = None
        
    log(
        "Starting fanctrl: "
        f"pin={cfg['gpio_pin']} chip={cfg['gpio_chip']} "
        f"backend={cfg['gpio_backend']} "
        f"poll={cfg['poll_interval_s']}s "
        f"{format_context(cfg, start_temp)}"
    )

    update_status(
        {
            "updated_at": now_iso(),
            "fan_on": state_on,
            "temp_c": None,
            "min_c": None,
            "max_c": None,
            "temp_on_c": cfg["temp_on_c"],
            "temp_off_c": cfg["temp_off_c"],
            "version": read_version(),
            "switchpoints": [],
        }
    )

    server_thread = threading.Thread(target=run_status_server, args=(status_port,), daemon=True)
    server_thread.start()

    while True:
        try:
            temp_c = read_temp_c(cfg["temp_path"])
        except Exception as exc:
            # Only warn every now and then or on first failure? 
            # Original code forced fan ON.
            log(f"ERROR: temp read failed: {exc}; forcing fan ON and retrying; {format_context(cfg, None)}")
            set_fan_state(gpio_ctx, cfg["active_high"], True)
            state_on = True
            update_status(
                {
                    "updated_at": now_iso(),
                    "fan_on": True,
                    "temp_c": None,
                    "min_c": min_temp,
                    "max_c": max_temp,
                    "temp_on_c": cfg["temp_on_c"],
                    "temp_off_c": cfg["temp_off_c"],
                    "error": "temp_read_failed",
                    "version": read_version(),
                    "switchpoints": list(switch_history),
                }
            )
            time.sleep(max(cfg["poll_interval_s"], 1.0))
            continue

        if min_temp is None or temp_c < min_temp:
            min_temp = temp_c
        if max_temp is None or temp_c > max_temp:
            max_temp = temp_c
        desired = state_on
        if temp_c >= cfg["temp_on_c"]:
            desired = True
        elif temp_c <= cfg["temp_off_c"]:
            desired = False

        now = time.time()
        if desired != state_on and (now - last_switch) >= cfg["min_switch_s"]:
            set_fan_state(gpio_ctx, cfg["active_high"], desired)
            state_on = desired
            last_switch = now
            log(f"Fan {'ON' if state_on else 'OFF'}; {format_context(cfg, temp_c)}")
            append_switch_event(switch_history, fan_on=state_on, temp_c=temp_c, cfg=cfg)
        elif cfg["log_each_read"]:
            log(f"Temp; fan {'ON' if state_on else 'OFF'}; {format_context(cfg, temp_c)}")

        update_status(
            {
                "updated_at": now_iso(),
                "fan_on": state_on,
                "temp_c": round(temp_c, 1),
                "min_c": round(min_temp, 1) if min_temp is not None else None,
                "max_c": round(max_temp, 1) if max_temp is not None else None,
                "temp_on_c": cfg["temp_on_c"],
                "temp_off_c": cfg["temp_off_c"],
                "version": read_version(),
                "switchpoints": list(switch_history),
            }
        )

        time.sleep(cfg["poll_interval_s"])


if __name__ == "__main__":
    raise SystemExit(main())
