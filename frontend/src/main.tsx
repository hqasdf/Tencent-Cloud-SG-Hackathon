import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./AuthenticatedApp";
import { ErrorBoundary } from "./components/ErrorBoundary";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>
);
