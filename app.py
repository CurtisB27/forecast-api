from flask import Flask, request, jsonify
from flask_cors import CORS
from datetime import datetime, timezone
import requests
import csv
import os

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

AVWX_API_KEY = "2bxdZ0RF-sWMMLU_3-APKf9gseNi-Va-Sc-n3wqg1zE"  # Your token

MONTHS = {
    'Jan':1, 'Feb':2, 'Mar':3, 'Apr':4, 'May':5, 'Jun':6,
    'Jul':7, 'Aug':8, 'Sep':9, 'Oct':10, 'Nov':11, 'Dec':12
}

def get_latlon_from_icao(icao):
    db_file = "airports.csv"
    if not os.path.exists(db_file):
        raise Exception("airports.csv not found")
    with open(db_file, newline='', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            if row['icao_code'].upper() == icao.upper():
                return float(row['latitude_deg']), float(row['longitude_deg'])
    raise ValueError(f"ICAO '{icao}' not found.")

def parse_line(line):
    parts = line.strip().split()
    if len(parts) < 5:
        raise ValueError("Line format incorrect")
    icao = parts[1]
    day_month = parts[2]
    day = int(day_month[:2])
    mon_str = day_month[2:]
    month = MONTHS.get(mon_str.capitalize())
    if not month:
        raise ValueError(f"Unknown month '{mon_str}'")
    time_str = parts[4]
    hour = int(time_str[:2])
    minute = int(time_str[2:])
    year = datetime.now(timezone.utc).year
    dt = datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
    return icao, dt

def parse_iso_z(dt_str):
    if not isinstance(dt_str, str):
        return None
    try:
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    except Exception:
        return None

def get_taf_wind(icao, dt):
    headers = {"Authorization": AVWX_API_KEY}
    url = f"https://avwx.rest/api/taf/{icao}?options=summary"
    try:
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
    except Exception:
        print(f"TAF fetch failed for {icao}")
        return None

    taf_data = r.json()

    if "forecast" not in taf_data or not taf_data["forecast"]:
        return None

    closest_period = None
    smallest_diff = None

    for period in taf_data["forecast"]:
        start = parse_iso_z(period.get("start_time"))
        end = parse_iso_z(period.get("end_time"))
        if not start or not end:
            continue

        if start <= dt <= end:
            try:
                return int(period["wind_direction"]), int(period["wind_speed"])
            except:
                continue
        else:
            diff = min(abs((dt - start).total_seconds()), abs((dt - end).total_seconds()))
            if smallest_diff is None or diff < smallest_diff:
                smallest_diff = diff
                closest_period = period

    if closest_period:
        try:
            return int(closest_period["wind_direction"]), int(closest_period["wind_speed"])
        except:
            pass

    return None

def get_openmeteo_forecast(icao, dt):
    lat, lon = get_latlon_from_icao(icao)
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "temperature_2m,pressure_msl,windspeed_10m,winddirection_10m",
        "timezone": "UTC",
        "start": dt.strftime("%Y-%m-%dT%H:00"),
        "end": dt.strftime("%Y-%m-%dT%H:00")
    }
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    data = r.json()
    hour_str = dt.strftime("%Y-%m-%dT%H:00")
    idx = data['hourly']['time'].index(hour_str)

    temp = round(data['hourly']['temperature_2m'][idx])
    pressure_mb = data['hourly']['pressure_msl'][idx]
    pressure_inhg = round(pressure_mb / 33.8639, 2)
    wind_speed = round(data['hourly']['windspeed_10m'][idx])
    wind_dir_raw = data['hourly']['winddirection_10m'][idx]
    wind_dir = int(round(wind_dir_raw / 10.0) * 10) % 360

    return temp, pressure_inhg, wind_speed, wind_dir

def format_forecast_line(icao, dt, wind_dir, wind_speed, temp, pressure_inhg):
    wind_str = f"{wind_dir:03d}{wind_speed:02d}kt"
    temp_str = f"{temp}c"
    pressure_str = f"{pressure_inhg:.2f}"
    formatted_time = dt.strftime("%d%b / %H%M UTC")
    return f"{icao.upper()} {formatted_time} {wind_str} {temp_str} {pressure_str}"

def get_forecast_with_taf_winds(icao, dt):
    taf = get_taf_wind(icao, dt)
    temp, pressure, om_speed, om_dir = get_openmeteo_forecast(icao, dt)
    wind_dir, wind_speed = taf if taf else (om_dir, om_speed)
    return format_forecast_line(icao, dt, wind_dir, wind_speed, temp, pressure)

@app.route('/forecast', methods=['POST'])
def forecast():
    data = request.json
    etd_line = data.get("etd")
    eta_line = data.get("eta")

    if not etd_line or not eta_line:
        return jsonify({"error": "Missing ETD or ETA"}), 400

    try:
        etd_icao, etd_dt = parse_line(etd_line)
        eta_icao, eta_dt = parse_line(eta_line)

        etd_forecast = get_forecast_with_taf_winds(etd_icao, etd_dt)
        eta_forecast = get_forecast_with_taf_winds(eta_icao, eta_dt)

        return jsonify({
            "etd_forecast": etd_forecast,
            "eta_forecast": eta_forecast
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True)
