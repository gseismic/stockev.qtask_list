import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import App from "./App";
import { OverviewPage } from "./pages/OverviewPage";
import { QueuesPage } from "./pages/QueuesPage";
import { QueueDetailPage } from "./pages/QueueDetailPage";
import { TasksPage } from "./pages/TasksPage";
import { WorkersPage } from "./pages/WorkersPage";
import { AlertsPage } from "./pages/AlertsPage";
import { GuidePage } from "./pages/GuidePage";
import { ThemeProvider } from "./theme";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ThemeProvider>
      <BrowserRouter>
        <Routes>
          <Route element={<App />}>
            <Route path="/" element={<OverviewPage />} />
            <Route path="/queues" element={<QueuesPage />} />
            <Route path="/queues/:name" element={<QueueDetailPage />} />
            <Route path="/tasks" element={<TasksPage />} />
            <Route path="/workers" element={<WorkersPage />} />
            <Route path="/alerts" element={<AlertsPage />} />
            <Route path="/guide" element={<GuidePage />} />
            <Route path="*" element={<OverviewPage />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </ThemeProvider>
  </React.StrictMode>,
);
