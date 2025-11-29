# debot
Fine-grain searching for Depop - filter clothing by measurements.

## Features
- **Seller Search**: Search a specific seller's listings by P2P (pit-to-pit) and length measurements
- **Browse All**: Scrape the Depop tops homepage to find sellers with matching items (filters for sellers with 50+ sold items)
- **Separate Tolerances**: Configure different tolerance ranges for P2P (±1") and length (±0.5") measurements
- **Real-time Streaming**: SSE-based streaming shows results as they're found
- **Cancellable Searches**: Stop any search in progress

## Prerequisites
- Python 3.8+
- Node.js 16+
- npm or yarn

## Setup

### Backend
1. Navigate to the backend directory:
   ```
   cd backend
   ```

2. Install Python dependencies:
   ```
   pip install -r requirements.txt
   ```

3. Install Playwright browsers:
   ```
   playwright install
   ```

4. Run the backend server:
   ```
   uvicorn main:app --reload
   ```
   The backend will be available at `http://localhost:8000`.

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

## Usage
1. Open the frontend in your browser at `http://localhost:5173`
2. Enter a seller username to search their listings, or leave blank and click "Browse All" to search the entire tops category
3. Set your target P2P and length measurements with optional tolerances
4. Click "Search" to start streaming results

## Project Structure
```
debot/
├── backend/
│   ├── main.py          # FastAPI endpoints and SSE streaming
│   ├── parser.py        # Measurement extraction from descriptions
│   ├── scraper.py       # Playwright scraping utilities
│   └── requirements.txt
└── frontend/
    └── src/
        ├── App.jsx           # Main React component
        ├── App.css           # Styles
        ├── components/       # SearchRow, Sidebar
        └── hooks/useStream.js # SSE streaming hook
```
