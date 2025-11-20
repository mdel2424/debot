# debot
Fine-grain searching for depop - filter by measurements.

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
- Open the frontend in your browser.
- The backend handles API requests for searching and filtering.
