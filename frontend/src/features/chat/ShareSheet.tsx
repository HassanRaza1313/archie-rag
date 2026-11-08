import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, Copy } from "lucide-react";
import { useEffect, useState } from "react";
import { Button, Dialog, ErrorNote, Group, Row, Switch } from "../../components/ui";
import { api, type KnowledgeBase } from "../../lib/api";

/** The link and embed snippet for a share token, on this server's own address. */
export function shareLinks(token: string, origin = window.location.origin): { link: string; embed: string } {
  return {
    link: `${origin}/share/${token}`,
    embed: `<script src="${origin}/widget.js" data-chatbot="${token}" async></script>`,
  };
}

export function CopyField({ label, value, help }: { label: string; value: string; help?: string }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 1500);
    return () => window.clearTimeout(timer);
  }, [copied]);
  return (
    <div className="mt-5">
      <div className="flex items-center justify-between px-1">
        <span className="text-sm font-medium text-slate">{label}</span>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => void navigator.clipboard?.writeText(value).then(() => setCopied(true))}
        >
          {copied ? <Check className="size-3.5" aria-hidden /> : <Copy className="size-3.5" aria-hidden />}
          {copied ? "Copied" : "Copy"}
        </Button>
      </div>
      <output className="mt-1 block rounded-[var(--radius-control)] bg-wall px-3 py-2 font-mono text-sm break-all select-all">
        {value}
      </output>
      {help && <p className="mt-1.5 px-1 text-xs text-slate">{help}</p>}
    </div>
  );
}

/** Share sheet: anyone with the link can ask this chatbot questions, on Archie or embedded in a website. */
export function ShareSheet({ kb, onClose }: { kb: KnowledgeBase; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [confirmReset, setConfirmReset] = useState(false);
  const share = useMutation({
    mutationFn: ({ enabled, reset = false }: { enabled: boolean; reset?: boolean }) => api.share(kb.id, enabled, reset),
    onSuccess: () => {
      setConfirmReset(false);
      void queryClient.invalidateQueries({ queryKey: ["kb", kb.id] });
      void queryClient.invalidateQueries({ queryKey: ["kbs"] });
    },
  });
  const token = share.data ? share.data.share_token : kb.share_token;
  const links = token ? shareLinks(token) : null;

  return (
    <Dialog
      open
      onClose={onClose}
      title={`Share ${kb.name}`}
      footer={
        <Button variant="primary" onClick={onClose}>
          Done
        </Button>
      }
    >
      <Group>
        <Row
          label="Anyone with the link can ask"
          detail="They see answers and their sources, nothing else: no settings, documents list or activity."
        >
          <Switch
            checked={Boolean(token)}
            disabled={share.isPending}
            onChange={(enabled) => share.mutate({ enabled })}
            label="Share with a link"
          />
        </Row>
      </Group>

      {links && (
        <>
          <CopyField label="Link" value={links.link} />
          <CopyField
            label="Embed on a website"
            value={links.embed}
            help="Paste it into any page, just before </body>. A chat button appears in the bottom corner."
          />
          <div className="mt-6 rounded-[var(--radius-group)] bg-wall px-4 py-3">
            {confirmReset ? (
              <div className="flex flex-wrap items-center justify-between gap-3">
                <p className="text-sm">The current link and every embed stop working.</p>
                <div className="flex gap-2">
                  <Button size="sm" onClick={() => setConfirmReset(false)}>
                    Cancel
                  </Button>
                  <Button
                    size="sm"
                    variant="danger"
                    busy={share.isPending}
                    onClick={() => share.mutate({ enabled: true, reset: true })}
                  >
                    Reset Link
                  </Button>
                </div>
              </div>
            ) : (
              <div className="flex flex-wrap items-center justify-between gap-3">
                <p className="text-sm text-slate">Sent it to the wrong people? Make a new link.</p>
                <Button size="sm" onClick={() => setConfirmReset(true)}>
                  Reset Link…
                </Button>
              </div>
            )}
          </div>
        </>
      )}
      <div className="mt-3">
        <ErrorNote error={share.error} />
      </div>
    </Dialog>
  );
}
