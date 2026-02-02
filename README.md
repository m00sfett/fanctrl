# fanctrl

A lightweight Python tool to control a fan based on system temperature, using `libgpiod`.

## Features
- **Backend**: Uses strict `libgpiod` (modern Linux GPIO). no legacy `RPi.GPIO` dependency.
- **Configurable**: Set thresholds, poll intervals, and GPIO lines via TOML config or environment variables.
- **Status API**: Simple HTTP endpoint to monitor current state.
- **Safety**: Failsafe defaults if config is invalid or sensor reading fails.

## Installation

### From Source
```bash
git clone https://github.com/yourusername/fanctrl.git
cd fanctrl/fanctrl
pip install .
```

## Usage

Run the tool directly:
```bash
fanctrl [path/to/config.toml]
```

Or via environment variables:
```bash
export FANCTRL_CONFIG="/path/to/config.toml"
fanctrl
```

## Configuration
Example `config.toml`:

```toml
[fan]
# GPIO Settings
gpio_chip = "gpiochip0"  # The GPIO character device
gpio_pin = 33            # The line offset (BCM pin number usually, NOT physical pin)
active_high = true       # Set to false for active-low fans

# Temperature Thresholds (Celsius)
temp_on_c = 55.0
temp_off_c = 45.0
temp_path = "/sys/class/thermal/thermal_zone0/temp"

# Timing
poll_interval_s = 5.0    # How often to check temp
min_switch_s = 10.0      # Minimum time to stay in one state (hysteresis)
log_each_read = false    # Log every poll or just changes
```

## PIN Mapping
**Important**: This tool uses `libgpiod`, which exclusively uses **chip line offsets** (often matching BCM numbers on RPi), not physical board pin numbers.
- If you used `RPi.GPIO` "BOARD" mode pin 33 previously, find the corresponding BCM number (e.g. BCM 13) and use that as `gpio_pin`.

## Status Endpoint
By default, a status server runs on port 9101:
`GET http://localhost:9101/status`

Returns:
```json
{
  "fan_on": true,
  "temp_c": 56.2,
  "temp_on_c": 55.0,
  "temp_off_c": 45.0,
  "version": "0.2.1"
}
```
