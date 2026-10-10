import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { LocaleProvider } from "./i18n";
import { Router } from "./router";
import "./index.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <LocaleProvider>
      <Router>
        <App />
      </Router>
    </LocaleProvider>
  </StrictMode>,
);
