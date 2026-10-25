import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, Navigate, RouterProvider } from "react-router-dom";
import { Layout } from "./components/Layout";
import { ActivityPage } from "./features/activity/ActivityPage";
import { BuildPage } from "./features/build/BuildPage";
import { ChatPage } from "./features/chat/ChatPage";
import { EvalPage } from "./features/eval/EvalPage";
import { HealthPage } from "./features/health/HealthPage";
import { HomePage } from "./features/kbs/HomePage";
import { NewChatbotPage } from "./features/new/NewChatbotPage";
import { SetupPage } from "./features/setup/SetupPage";
import "./styles.css";

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 5_000, refetchOnWindowFocus: false, retry: 1 } },
});

const router = createBrowserRouter([
  {
    element: <Layout />,
    children: [
      { path: "/", element: <HomePage /> },
      { path: "/new", element: <NewChatbotPage /> },
      { path: "/health", element: <HealthPage /> },
      { path: "/telemetry", element: <Navigate to="/health" replace /> },
      { path: "/kb/:kbId", element: <Navigate to="ask" replace /> },
      { path: "/kb/:kbId/ask", element: <ChatPage /> },
      { path: "/kb/:kbId/chat", element: <Navigate to="../ask" relative="path" replace /> },
      { path: "/kb/:kbId/setup", element: <SetupPage /> },
      { path: "/kb/:kbId/builds", element: <BuildPage /> },
      { path: "/kb/:kbId/builds/:jobId", element: <BuildPage /> },
      { path: "/kb/:kbId/evaluate", element: <EvalPage /> },
      { path: "/kb/:kbId/activity", element: <ActivityPage /> },
      { path: "*", element: <Navigate to="/" replace /> },
    ],
  },
]);

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </StrictMode>,
  );
}
