---

## 🛒 Shop Route Optimizer

A full-stack web application that finds shops near you and builds an optimised multi-stop route — comparing car, pedestrian and public-transport modes, each optimised for the shortest distance or shortest travel time.

---

## 📁 Project Structure

```
shop_router/
├── app.py                  # Flask backend (API + TSP solver)
├── requirements.txt        # Python dependencies
├── .env.example            # Environment variable template
├── .gitignore
├── Dockerfile
├── docker-compose.yml
├── templates/
│   └── index.html          # Single-page frontend (Yandex Maps JS API)
└── static/
    ├── css/
    │   └── style.css       # All styles
    └── js/
        └── app.js          # Frontend logic
```

---

## 🔑 Obtaining Yandex Maps API Keys

You need one key that covers both the JavaScript API and the HTTP Geocoder.

1. Go to https://developer.tech.yandex.ru/ and sign in with your Yandex account.
2. Click "Connect APIs" → select:
   - ✅ JavaScript API and HTTP Geocoder
3. Fill in the project form (name, URL = http://localhost:5000 for local dev).
4. After approval (usually instant) copy the key from the dashboard.
5. Paste it into your .env file as both YANDEX_MAPS_JS_KEY and YANDEX_GEOCODER_KEY.

> Free tier limits (as of 2025):
> - 1,000 Geocoder API requests/day
> - Unlimited JS API map loads

---

## 🚀 Running Locally (without Docker)

### Prerequisites
- Python 3.10+
- pip

### Steps

```bash
# 1. Clone / download the project
cd shop_router

# 2. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set up environment variables
cp .env.example .env
# Edit .env and paste your Yandex API key

# 5. Run the development server
python app.py
```

Open http://localhost:5000 in your browser.

---

## 🐳 Running with Docker (Recommended for Production)

```bash
# 1. Build and start
cp .env.example .env   # fill in your keys first
docker compose up --build -d

# 2. Check logs
docker compose logs -f

# 3. Stop
docker compose down
```

App is available at http://localhost:5000.

---

## ☁️ Deployment Options (Free, Accessible from Russia)

### Option A — Yandex Cloud (Serverless Containers) ⭐ Recommended

Step-by-step:

```bash
# 1. Install Yandex Cloud CLI
curl -sSL https://storage.yandexcloud.net/yandexcloud-yc/install.sh | bash
yc init   # follow the prompts to log in

# 2. Create a container registry
yc container registry create --name shop-router

# 3. Build and push Docker image
REGISTRY_ID=$(yc container registry get shop-router --format json | jq -r .id)
docker build -t cr.yandex/${REGISTRY_ID}/shop-router:latest .
docker push cr.yandex/${REGISTRY_ID}/shop-router:latest

# 4. Create a serverless container
yc serverless container create \
  --name shop-router \
  --image cr.yandex/${REGISTRY_ID}/shop-router:latest \
  --cores 1 \
  --memory 256MB \
  --environment YANDEX_MAPS_JS_KEY=your_key \
  --environment YANDEX_GEOCODER_KEY=your_key \
  --concurrency 5

# 5. Make it public
yc serverless container allow-unauthenticated-invoke --name shop-router

# 6. Get your public URL
yc serverless container get --name shop-router --format json | jq -r .url
```

**Free tier:** 1,000,000 invocations/month, 10 GB-hours/month.

---

### Option B — Timeweb Cloud (VPS)

1. Register at https://timeweb.cloud → create a VPS (Ubuntu 22.04, minimum 1 vCPU/1 GB RAM, ~150 RUB/month but new users get free credits).
2. SSH into the server:

```bash
# Install Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER

# Clone your repo and deploy
git clone https://github.com/YOU/shop-router.git
cd shop-router
cp .env.example .env && nano .env   # paste keys
docker compose up -d

# Optional: set up Nginx reverse proxy on port 80
sudo apt install nginx -y
sudo nano /etc/nginx/sites-available/shop-router
```

Nginx config:

```nginx
server {
    listen 80;
    server_name your-server-ip;
    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

---

### Option C — Local Network (Docker + ngrok)

For quick sharing without a server:

```bash
# Start the app
docker compose up -d

# Expose it via ngrok (free tier)
# Download from https://ngrok.com → 
ngrok http 5000
# You get a public URL like https://xxxx.ngrok.io
```

---

## 🧠 How the Routing Optimisation Works

### Problem
Given a start location S and N shops {P₁…Pₙ}, find the visit order that minimises total cost (distance or travel time). This is the classic Travelling Salesman Problem (TSP) which is NP-hard.

### Our Approach

#### Distance matrix
The backend builds an N×N matrix of Haversine (great-circle) distances between all points. This is an excellent straight-line approximation and requires zero API calls.

For the time matrix, each distance is divided by a mode-specific speed:

| Mode         | Assumed speed |
|--------------|---------------|
| Auto         | 40 km/h (urban average) |
| Pedestrian   | 5 km/h |
| Mass transit | 20 km/h (stops + waiting) |

#### TSP Solver
- N ≤ 10 points → Exact brute-force via itertools.permutations. Tries all (N-1)! orderings, picks the minimum. For N=10 that's 9! = 362,880 permutations — runs in < 1 second in Python.
- N > 10 points → Nearest-Neighbour heuristic (greedy, O(N²)) followed by a 2-opt local search improvement loop that swaps edge pairs until no improvement is found. Typical solution quality: within 5–15% of optimal.

#### 6 route variants
3 modes × 2 criteria = 6 TSP runs

Each run uses the appropriate cost matrix (distance or estimated time), so the waypoint order may differ between, e.g., "car/distance" and "car/time".

#### Actual route display
The ordered waypoints are passed to the Yandex Maps JS API `ymaps.multiRouter.MultiRoute` on the frontend, which calls Yandex's real routing engine to draw turn-by-turn routes and return accurate distances and durations shown in the results table.

---

## 🛠️ API Reference

### POST /api/search-shops

```json
// Request
{
  "shops":  ["Пятёрочка", "Аптека"],
  "origin": {"lat": 55.751, "lon": 37.618}
}

// Response
{
  "shops": [
    {
      "query": "Пятёрочка",
      "results": [
        {"name": "Пятёрочка", "address": "ул. Ленина, 1", "lat": 55.76, "lon": 37.62, "distance_m": 1200}
      ]
    }
  ]
}
```

### POST /api/solve-tsp

```json
// Request — points[0] must be the start location
{
  "points": [
    {"lat": 55.751, "lon": 37.618, "label": "Старт"},
    {"lat": 55.760, "lon": 37.630, "label": "Пятёрочка"}
  ]
}

// Response
{
  "results": [
    {
      "mode":      "auto",
      "criterion": "distance",
      "order":     [0, 1],
      "cost":      1320.5,
      "unit":      "m",
      "waypoints": [{"lat":…, "lon":…, "label":…}, …]
    },
    // … 5 more entries
  ]
}
```

---

## ⚙️ Configuration

| Variable | Description | Required |
|----------|-------------|----------|
| YANDEX_MAPS_JS_KEY | Yandex Maps JS API key | ✅ |
| YANDEX_GEOCODER_KEY | Yandex Geocoder HTTP API key | ✅ |
| FLASK_DEBUG | Enable debug mode (true/false) | No |
| PORT | Server port (default 5000) | No |