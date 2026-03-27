# debot
Fine-grain searching for Depop - filter clothing by measurements.

## Features
- **Seller Search**: Search a specific seller's listings by P2P (pit-to-pit) and length measurements
- **Browse All**: Scrape the Depop tops homepage until the requested match target is reached or the live page is exhausted (filters for sellers with 50+ sold items)
- **Separate Tolerances**: Configure different tolerance ranges for P2P (±0.5") and length (±1.25") measurements
- **Real-time Streaming**: SSE-based streaming shows results as they're found
- **Cancellable Searches**: Stop any search in progress
- **Ubuntu Launcher**: Start the backend and frontend in two GNOME Terminal tabs with one script

## Prerequisites
- Python 3.13.11
- Node.js 16+
- npm or yarn

This repo pins Python with [`.python-version`](/home/mdel2424/dev/debot/.python-version) and expects a project-local virtualenv at `.venv/`.

## Setup

### Backend
1. Create and activate the project virtualenv from the repo root:
   ```
   python3 -m venv .venv
   source .venv/bin/activate
   ```

2. Navigate to the backend directory:
   ```
   cd backend
   ```

3. Install Python dependencies:
   ```
   pip install -r requirements.txt
   ```

4. Install Playwright browsers:
   ```
   playwright install
   ```

5. Run the backend server:
   ```
   uvicorn main:app --reload
   ```
   The backend will be available at `http://localhost:8000`.

VS Code is configured to use `.venv/bin/python` for discovery and testing. If it falls back to your system interpreter, re-run `Python: Select Interpreter` and choose the repo `.venv`.

### Frontend
1. Navigate to the frontend directory:
   ```
   cd frontend
   ```

2. Install Node.js dependencies:
   ```
   npm install
   ```

3. Run the development server:
   ```
   npm run dev
   ```
   The frontend will be available at `http://localhost:5173`.

### Ubuntu Launcher
Run the repo launcher from the project root to start both dev servers in separate GNOME Terminal tabs:

```
./run-debot.sh
```

To install a real clickable Ubuntu launcher on your Desktop and in the app menu:

```
./install-ubuntu-launcher.sh
```

After that, use `Run Debot.desktop` from your Desktop or app launcher.

## Usage
1. Open the frontend in your browser at `http://localhost:5173`
2. Enter a seller username to search their listings, or leave blank and click "Browse All" to search the entire tops category
3. Set your target P2P and length measurements with optional tolerances
   Default search values are `21.5 ±0.5 x 27.25 ±1.25`
4. Click "Search" to start streaming results

## Testing
Run the offline regression suite after scraper or parsing changes:

```
.venv/bin/python -m unittest discover -s backend/tests
```

Run the live Depop smoke test only when you want to verify current site behavior and selector/scroll drift:

```
DEPOP_LIVE_SMOKE=1 .venv/bin/python -m unittest discover -s backend/tests -p 'test_live_depop_smoke.py'
```

## Project Structure
```
debot/
├── backend/
│   ├── main.py          # FastAPI endpoints and SSE streaming
│   ├── parser.py        # Measurement extraction from descriptions
│   ├── scraper.py       # Playwright scraping utilities
│   ├── requirements.txt
│   └── tests/           # Offline regression coverage
├── frontend/
│   └── src/
│       ├── App.jsx           # Main React component
│       ├── App.css           # Styles
│       ├── components/       # SearchRow, Sidebar
│       └── hooks/useStream.js # SSE streaming hook
├── install-ubuntu-launcher.sh # Installs a trusted Ubuntu launcher
├── run-debot.desktop   # Clickable Ubuntu launcher
└── run-debot.sh         # Ubuntu launcher for frontend + backend tabs
```
