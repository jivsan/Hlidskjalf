// Type system: Archivo (variable, wght+wdth) is the human interface — nav,
// headings, labels, prose, the wordmark. JetBrains Mono is reserved for machine
// truth — metrics, IDs, UPIDs, the faceplate. The split is the concept.
import "@fontsource-variable/archivo/standard.css";
import "@fontsource/jetbrains-mono/400.css";
import "@fontsource/jetbrains-mono/500.css";
import "@fontsource/jetbrains-mono/700.css";
import "./index.css";

import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./App";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
