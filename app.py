"""
Shop Route Optimizer — Flask Backend
=====================================
Endpoints:
  POST /api/search-shops   — find shops via Yandex Suggest + Geocoder
  POST /api/solve-tsp      — solve Traveling Salesman for a set of points
  GET  /                   — serve the main page
"""

import os
import math
import itertools
import logging
from typing import Optional

import requests
from flask import Flask, render_template, jsonify, request
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)

# ----------------------------------------------------------------------
#  API keys
# ----------------------------------------------------------------------
YANDEX_GEOCODER_KEY = os.environ.get("YANDEX_GEOCODER_KEY", "")   # ключ для HTTP Геокодера
YANDEX_MAPS_JS_KEY  = os.environ.get("YANDEX_MAPS_JS_KEY", "")    # для JavaScript API
YANDEX_SUGGEST_KEY  = os.environ.get("YANDEX_SUGGEST_KEY", "")    # ключ для API Suggest (Геосаджест)

SUGGEST_URL = "https://suggest-maps.yandex.ru/v1/suggest"
GEOCODER_URL = "https://geocode-maps.yandex.ru/1.x/"
SEARCH_RADIUS_M = 10_000  # 10 км

# ----------------------------------------------------------------------
#  Geometry helpers
# ----------------------------------------------------------------------
def haversine(p1: tuple[float, float], p2: tuple[float, float]) -> float:
    """Return great-circle distance in metres between (lat,lon) pairs."""
    R = 6_371_000
    lat1, lon1 = math.radians(p1[0]), math.radians(p1[1])
    lat2, lon2 = math.radians(p2[0]), math.radians(p2[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def build_distance_matrix(points: list[tuple[float, float]]) -> list[list[float]]:
    """NxN Haversine distance matrix (metres)."""
    n = len(points)
    return [[haversine(points[i], points[j]) for j in range(n)] for i in range(n)]


SPEED = {"auto": 11.1, "pedestrian": 1.4, "masstransit": 5.5}  # m/s

def time_matrix(dist_matrix: list[list[float]], mode: str) -> list[list[float]]:
    """Convert distance matrix to estimated time matrix (seconds)."""
    spd = SPEED.get(mode, 5.0)
    return [[d / spd for d in row] for row in dist_matrix]


# ----------------------------------------------------------------------
#  TSP Solvers (unchanged)
# ----------------------------------------------------------------------
def tsp_brute_force(matrix: list[list[float]], start: int = 0) -> tuple[list[int], float]:
    n = len(matrix)
    cities = [i for i in range(n) if i != start]
    best_cost = math.inf
    best_tour: list[int] = []
    for perm in itertools.permutations(cities):
        tour = [start] + list(perm) + [start]
        cost = sum(matrix[tour[i]][tour[i + 1]] for i in range(len(tour) - 1))
        if cost < best_cost:
            best_cost = cost
            best_tour = tour[:-1]
    return best_tour, best_cost


def tsp_nearest_neighbor(matrix: list[list[float]], start: int = 0) -> tuple[list[int], float]:
    n = len(matrix)
    unvisited = set(range(n)) - {start}
    tour = [start]
    current = start
    total = 0.0
    while unvisited:
        nearest = min(unvisited, key=lambda j: matrix[current][j])
        total += matrix[current][nearest]
        tour.append(nearest)
        current = nearest
        unvisited.remove(nearest)
    total += matrix[current][start]
    return tour, total


def two_opt(tour: list[int], matrix: list[list[float]]) -> tuple[list[int], float]:
    improved = True
    best = tour[:]
    n = len(best)

    def tour_cost(t: list[int]) -> float:
        return sum(matrix[t[i]][t[(i + 1) % n]] for i in range(n))

    best_cost = tour_cost(best)
    while improved:
        improved = False
        for i in range(1, n - 1):
            for j in range(i + 1, n):
                new_tour = best[:i] + best[i:j+1][::-1] + best[j+1:]
                cost = tour_cost(new_tour)
                if cost < best_cost - 1e-9:
                    best = new_tour
                    best_cost = cost
                    improved = True
    return best, best_cost


def solve_tsp(points: list[tuple[float, float]], mode: str, criterion: str) -> dict:
    n = len(points)
    dist_mat = build_distance_matrix(points)
    if criterion == "time":
        matrix = time_matrix(dist_mat, mode)
        unit = "s"
    else:
        matrix = dist_mat
        unit = "m"
    if n <= 10:
        tour, cost = tsp_brute_force(matrix, start=0)
    else:
        tour, cost = tsp_nearest_neighbor(matrix, start=0)
        tour, cost = two_opt(tour, matrix)
    return {"order": tour, "cost": round(cost, 1), "unit": unit}


# ----------------------------------------------------------------------
#  Yandex Suggest + Geocoder for shop search
# ----------------------------------------------------------------------
def geocode_address(address: str, origin: tuple[float, float]) -> Optional[dict]:
    if not YANDEX_GEOCODER_KEY:
        log.error("YANDEX_GEOCODER_KEY is missing")
        return None

    params = {
        "apikey": YANDEX_GEOCODER_KEY,
        "geocode": address,
        "format": "json",
        "lang": "ru_RU",
        "results": 1,
        "ll": f"{origin[1]},{origin[0]}",
    }
    try:
        resp = requests.get(GEOCODER_URL, params=params, timeout=8)
        resp.raise_for_status()
        data = resp.json()
        features = data["response"]["GeoObjectCollection"]["featureMember"]
        if not features:
            log.info(f"No geocoder results for address: {address}")
            return None
        geo = features[0]["GeoObject"]
        lon, lat = map(float, geo["Point"]["pos"].split())
        return {
            "name": geo.get("name", address),
            "address": geo.get("description", ""),
            "lat": lat,
            "lon": lon,
        }
    except Exception as exc:
        log.warning("Geocoder error for address %s: %s", address, exc)
        return None


def search_organizations(query: str, origin: tuple[float, float], radius_m: int = SEARCH_RADIUS_M) -> list[dict]:
    if not YANDEX_SUGGEST_KEY:
        log.error("YANDEX_SUGGEST_KEY is missing")
        return []

    spn_deg = (radius_m / 111000) * 2  # градусы для квадрата ~ диаметр radius_m
    params = {
        "apikey": YANDEX_SUGGEST_KEY,
        "text": query,
        "ll": f"{origin[1]},{origin[0]}",
        "spn": f"{spn_deg},{spn_deg}",
        "types": "biz",
        "results": 5,
        "lang": "ru_RU",
    }

    try:
        resp = requests.get(SUGGEST_URL, params=params, timeout=8)
        resp.raise_for_status()
        data = resp.json()
        log.info(f"Suggest response for '{query}': {data}")
    except Exception as exc:
        log.warning("Suggest API error for %r: %s", query, exc)
        return []

    suggest_results = data.get("results", [])
    if not suggest_results:
        return []

    results = []
    for item in suggest_results:
        title = item.get("title", {}).get("text", query)
        subtitle = item.get("subtitle", {}).get("text", "")
        # Извлекаем адрес после "· "
        if "· " in subtitle:
            address = subtitle.split("· ", 1)[1]
        else:
            address = subtitle

        # Формируем полный адрес с городом (Москва, так как origin в Москве)
        # Можно также определить город по origin (обратным геокодированием), но для простоты добавим Москву.
        full_address = f"{address}, Москва"

        # Геокодируем адрес
        geo_data = geocode_address(full_address, origin)
        if not geo_data:
            log.info(f"Geocoding failed for: {full_address}")
            continue

        lat, lon = geo_data["lat"], geo_data["lon"]
        dist = haversine(origin, (lat, lon))
        if dist <= radius_m:
            results.append({
                "name": title,
                "address": address,
                "lat": lat,
                "lon": lon,
                "distance_m": round(dist),
            })
        else:
            log.info(f"Distance {dist:.0f} > {radius_m} for {address}")

    return results


# ----------------------------------------------------------------------
#  Flask routes
# ----------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html", maps_js_key=YANDEX_MAPS_JS_KEY)


@app.route("/api/search-shops", methods=["POST"])
def api_search_shops():
    body = request.get_json(force=True)
    shops: list[str] = body.get("shops", [])
    origin_raw = body.get("origin", {})
    radius = body.get("radius", SEARCH_RADIUS_M)

    if not shops or "lat" not in origin_raw:
        return jsonify({"error": "Missing 'shops' or 'origin'"}), 400

    origin = (float(origin_raw["lat"]), float(origin_raw["lon"]))
    response_data = []

    for shop_query in shops[:10]:   # hard cap at 10
        results = search_organizations(shop_query, origin, radius_m=radius)
        response_data.append({"query": shop_query, "results": results})
        log.info("Search '%s' → %d result(s)", shop_query, len(results))

    return jsonify({"shops": response_data})


@app.route("/api/solve-tsp", methods=["POST"])
def api_solve_tsp():
    body = request.get_json(force=True)
    raw_points: list[dict] = body.get("points", [])

    if len(raw_points) < 2:
        return jsonify({"error": "Need at least 2 points (start + 1 shop)"}), 400
    if len(raw_points) > 11:
        return jsonify({"error": "Maximum 10 shops supported"}), 400

    points = [(p["lat"], p["lon"]) for p in raw_points]
    results = []

    modes = ["auto", "pedestrian", "masstransit"]
    criterion = "time"

    for mode in modes:
        tsp = solve_tsp(points, mode, criterion)
        ordered_waypoints = [raw_points[i] for i in tsp["order"]]
        results.append({
            "mode": mode,
            "criterion": criterion,
            "order": tsp["order"],
            "cost": tsp["cost"],
            "unit": tsp["unit"],   # будет "s"
            "waypoints": ordered_waypoints,
        })
        log.info("TSP %s/%s → cost %.1f %s", mode, criterion, tsp["cost"], tsp["unit"])

    return jsonify({"results": results})


@app.route("/api/config")
def api_config():
    return jsonify({"mapsKey": YANDEX_MAPS_JS_KEY})


# ----------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)