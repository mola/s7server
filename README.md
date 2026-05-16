# s7server

Simulated Siemens S7 PLC server using [python-snap7](https://pypi.org/project/python-snap7/). Exposes S7 memory areas (DB, M, I, Q) over TCP so S7 clients can read values without real hardware.

## Install & Run

```bash
pip install python-snap7
python s7run.py
```

Starts on port 1105 with simulation writing to DB1 every second. Ctrl+C to stop.

## Default Tags (DB1)

| Label        | Offset | Type   | Value           | Notes             |
|--------------|--------|--------|-----------------|-------------------|
| Counter      | 0      | WORD   | 0→65535 (↑1/s)  | Auto-incrementing |
| Speed        | 2      | INT    | 1500 RPM        |                   |
| Temperature  | 4      | REAL   | 45.5 °C         |                   |
| Pressure     | 8      | REAL   | 101.3 bar       |                   |
| FlowRate     | 12     | REAL   | 12.5 L/min      |                   |
| Running      | 16.0   | BOOL   | True            |                   |
| Alarm        | 17.0   | BOOL   | False           |                   |
| Status       | 18     | WORD   | 1               |                   |
| Hours        | 20     | DINT   | 12345           |                   |
| Power        | 24     | REAL   | 7.5 kW          |                   |
| Humidity     | 28     | INT    | 65 %            |                   |
# s7server
