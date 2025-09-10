// src/App.jsx
import { useState } from "react";

export default function App() {
  const [category, setCategory] = useState("tops");
  const [val1, setVal1] = useState("");
  const [val2, setVal2] = useState("");

  async function handleSubmit(e) {
    e.preventDefault();
    const payload = {
      category,
      measurements: { first: Number(val1), second: Number(val2) },
    };
    const res = await fetch("/api/crawl", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: 'p2p length chest "pit to pit"', maxItems: 24, maxAgeDays: 31 }),
    });
    const data = await res.json();
    console.log(data.items);
  }

  return (
    <div className="min-h-screen grid place-items-center bg-gray-50">
      {/* remove w-full here so it doesn't stretch; use mx-auto to center */}
      <div className="max-w-md w-full px-4 mx-auto">
        <h1 className="text-2xl font-bold mb-6 text-center">debot</h1>

        <form
          onSubmit={handleSubmit}
          className="space-y-6 rounded-lg border p-4 bg-white shadow w-full"
        >
          {/* Group 1: radio cards */}
          <div className="flex gap-4">
            {["tops", "bottoms"].map((c) => (
              <label
                key={c}
                className={`flex-1 p-3 border rounded-lg cursor-pointer text-center ${
                  category === c ? "bg-blue-600 text-white" : "bg-gray-100 hover:bg-gray-200"
                }`}
              >
                <input
                  type="radio"
                  name="category"
                  value={c}
                  checked={category === c}
                  onChange={() => setCategory(c)}
                  className="hidden"
                />
                {c}
              </label>
            ))}
          </div>

          {/* Group 2: two numeric inputs */}
          <div className="grid grid-cols-2 gap-4">
            <input
              type="number"
              placeholder="Chest (in)"
              value={val1}
              onChange={(e) => setVal1(e.target.value)}
              className="border rounded-lg p-2 text-center"
            />
            <input
              type="number"
              placeholder="Length (in)"
              value={val2}
              onChange={(e) => setVal2(e.target.value)}
              className="border rounded-lg p-2 text-center"
            />
          </div>

          {/* Search button */}
          <button
            type="submit"
            className="w-full bg-blue-600 text-white py-2 rounded-lg hover:bg-blue-700"
          >
            Search
          </button>
        </form>
      </div>
    </div>
  );
}
