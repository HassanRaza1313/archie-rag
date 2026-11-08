import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, Navigate, RouterProvider, useParams } from "react-router-dom";
import { Layout, ManageLayout } from "./components/Layout";
import { InvitePage, RequireSignIn } from "./features/account/auth";
import { PeoplePage } from "./features/account/PeoplePage";
import { ActivityPage } from "./features/activity/ActivityPage";
import { BuildPage } from "./features/build/BuildPage";
import { ChatPage } from "./features/chat/ChatPage";
import { DocumentsPage } from "./features/documents/DocumentsPage";
import { EvalPage } from "./features/eval/EvalPage";
import { HealthPage } from "./features/health/HealthPage";
import { HomePage } from "./features/kbs/HomePage";
import { NewChatbotPage } from "./features/new/NewChatbotPage";
import { SetupPage } from "./features/setup/SetupPage";
import { SharedChatPage } from "./features/share/SharedChatPage";
import "./styles.css";

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 5_000, refetchOnWindowFocus: false, retry: 1 } },
});

/** Older links (bookmarks, alerts) keep working after the move to Ask + Manage. */
function Moved({ to }: { to: string }) {
  const { kbId, jobId } = useParams();
  return <Navigate to={`/kb/${kbId}/${to}${jobId ? `/${jobId}` : ""}`} replace />;
}

const router = createBrowserRouter([
  // Shared links stand alone: no sidebar, no Manage, nothing but the chatbot.
  { path: "/share/:token", element: <SharedChatPage /> },
  { path: "/invite/:token", element: <InvitePage /> },
  {
    element: (
      <RequireSignIn>
        <Layout />
      </RequireSignIn>
    ),
    children: [
      { path: "/", element: <HomePage /> },
      { path: "/new", element: <NewChatbotPage /> },
      { path: "/status", element: <HealthPage /> },
      { path: "/people", element: <PeoplePage /> },
      { path: "/health", element: <Navigate to="/status" replace /> },
      { path: "/telemetry", element: <Navigate to="/status" replace /> },
      { path: "/kb/:kbId", element: <Navigate to="ask" replace /> },
      { path: "/kb/:kbId/ask", element: <ChatPage /> },
      {
        path: "/kb/:kbId/manage",
        element: <ManageLayout />,
        children: [
          { index: true, element: <Navigate to="documents" replace /> },
          { path: "documents", element: <DocumentsPage /> },
          { path: "settings", element: <SetupPage /> },
          { path: "builds", element: <BuildPage /> },
          { path: "builds/:jobId", element: <BuildPage /> },
          { path: "quality", element: <EvalPage /> },
          { path: "activity", element: <ActivityPage /> },
        ],
      },
      { path: "/kb/:kbId/chat", element: <Moved to="ask" /> },
      { path: "/kb/:kbId/setup", element: <Moved to="manage/settings" /> },
      { path: "/kb/:kbId/documents", element: <Moved to="manage/documents" /> },
      { path: "/kb/:kbId/builds", element: <Moved to="manage/builds" /> },
      { path: "/kb/:kbId/builds/:jobId", element: <Moved to="manage/builds" /> },
      { path: "/kb/:kbId/evaluate", element: <Moved to="manage/quality" /> },
      { path: "/kb/:kbId/activity", element: <Moved to="manage/activity" /> },
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
