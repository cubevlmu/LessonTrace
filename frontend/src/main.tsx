import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { initTheme } from "./lib/theme";
import "./styles/app.css";

initTheme();

// 正式应用由同源 /app 提供；独立模拟演示在 /app/demo，不通过 file:// 运行。

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter basename="/app">
      <App />
    </BrowserRouter>
  </React.StrictMode>,
);
