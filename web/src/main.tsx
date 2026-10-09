import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { fetchHealth, type HealthResult } from "./health";
import "./index.css";

function App() {
  const [result, setResult] = useState<HealthResult | null>(null);

  useEffect(() => {
    void fetchHealth().then(setResult);
  }, []);

  const label =
    result === null
      ? "Prüfe …"
      : result.kind === "ok"
        ? "Alles in Ordnung"
        : result.kind === "degraded"
          ? `Eingeschränkt: Datenbank ${result.health.db}, Redis ${result.health.redis}`
          : "API nicht erreichbar";

  return (
    <main className="min-h-screen bg-slate-900 text-slate-100 flex items-center justify-center px-4">
      <div className="max-w-md text-center">
        <h1 className="text-3xl font-bold">GG-Bot</h1>
        <p className="mt-2 text-slate-400">Das Dashboard kommt in Phase 10.</p>
        <p className="mt-6 text-sm" role="status">
          {label}
        </p>
      </div>
    </main>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
