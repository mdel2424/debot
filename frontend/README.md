# Debot Frontend

See the [root README](../README.md) for backend setup, configuration,
persistence behavior, and browser tests.

```bash
npm ci
npm run dev
```

`npm test` runs Node streaming regressions. `npm run lint` checks the React code,
and `npm run build` verifies the production bundle.

Development uses Vite's `/api` proxy. Set `VITE_API_BASE` only for a separate API origin.
