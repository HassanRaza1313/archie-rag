import { useQuery } from "@tanstack/react-query";
import { Link2Off } from "lucide-react";
import { useParams } from "react-router-dom";
import { EmptyState, Skeleton } from "../../components/ui";
import { api, shareBase } from "../../lib/api";
import { ChatView } from "../chat/ChatPage";

/** What anyone with a share link sees: the chatbot and nothing else. Also the page inside the website widget. */
export function SharedChatPage() {
  const { token = "" } = useParams();
  const info = useQuery({ queryKey: ["shared", token], queryFn: () => api.shared(token), retry: false });

  return (
    <div className="flex h-dvh flex-col bg-wall text-ink">
      {info.isLoading ? (
        <div className="px-8 py-10">
          <Skeleton />
        </div>
      ) : info.data ? (
        <ChatView
          base={shareBase(token)}
          storageKey={`share.${token}`}
          name={info.data.name}
          documents={info.data.documents}
          built={info.data.ready}
          notReady={
            <EmptyState title={`${info.data.name} isn't ready yet`}>
              It's still reading its documents. Try again in a few minutes.
            </EmptyState>
          }
        />
      ) : (
        <EmptyState icon={<Link2Off />} title="This link no longer works">
          Its owner stopped sharing it or made a new link. Ask them for the current one.
        </EmptyState>
      )}
      <footer className="flex shrink-0 justify-center pb-2 text-xs text-faint">
        Answers by Archie, from its documents
      </footer>
    </div>
  );
}
