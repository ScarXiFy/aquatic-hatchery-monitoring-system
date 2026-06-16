# Aquatic Hatchery Monitoring System

A Flask dashboard for live aquatic hatchery sensor monitoring with SQLite storage, Flask-SocketIO updates, simulated readings, controls, alerts, and Chart.js trend graphs.

## Run Locally

```powershell
pip install -r requirements.txt
python run.py
```

Open `http://127.0.0.1:5000`.

## Pages

- `/` - live dashboard with sensor cards, controls, valve toggles, 24h mini charts, and thresholds
- `/graph` - full Day/Week charts for temperature, dissolved oxygen, salinity, and pH

## API

- `GET /api/readings/latest`
- `GET /api/readings/history?range=day|week`
- `GET /api/thresholds`
- `POST /api/controls/valves/<source|drain>`
- `POST /api/controls/sliders`

## Sensor Simulation

Simulated sensor logic lives in `app/sensors/simulator.py` so it can be swapped later for Raspberry Pi GPIO or real sensor integrations.
