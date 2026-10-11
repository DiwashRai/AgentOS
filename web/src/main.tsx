import "./styles/fonts.css";
import "./styles/tokens.css";
import "./styles/base.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./app/App";
import { watchVisibility } from "./app/motion";

const root = document.getElementById("root");
if (!root) throw new Error("missing #root");

watchVisibility();

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
