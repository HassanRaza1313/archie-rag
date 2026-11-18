import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Button, ErrorNote, Field, Group, Row, Segmented, Select } from "../../components/ui";
import { type ApiKey, api } from "../../lib/api";
import { relativeTime } from "../../lib/format";
import { can, useMe } from "../account/auth";
import { CopyField } from "../chat/ShareSheet";

const SCOPES = { ask: "Ask questions", manage: "Ask and manage" } as const;

function KeyRow({ apiKey, chatbot }: { apiKey: ApiKey; chatbot: string }) {
  const queryClient = useQueryClient();
  const revoke = useMutation({
    mutationFn: () => api.revokeKey(apiKey.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["api-keys"] }),
  });
  const used = apiKey.last_used_at ? `used ${relativeTime(apiKey.last_used_at)}` : "never used";
  return (
    <>
      <Row label={apiKey.name} detail={[chatbot, SCOPES[apiKey.scope], `${apiKey.prefix}…`, used].join(" · ")}>
        <Button size="sm" busy={revoke.isPending} onClick={() => revoke.mutate()}>
          Revoke
        </Button>
      </Row>
      {revoke.error && (
        <div className="px-4 pb-3">
          <ErrorNote error={revoke.error} />
        </div>
      )}
    </>
  );
}

function NewKey({ onDone }: { onDone: () => void }) {
  const queryClient = useQueryClient();
  const kbs = useQuery({ queryKey: ["kbs"], queryFn: api.listKbs });
  const [name, setName] = useState("");
  const [kbId, setKbId] = useState("");
  const [scope, setScope] = useState<ApiKey["scope"]>("ask");
  const create = useMutation({
    mutationFn: () => api.createKey({ name: name.trim(), kb_id: kbId || null, scope }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["api-keys"] }),
  });
  if (create.data)
    return (
      <div className="px-4 pb-4">
        <CopyField
          label={`${create.data.key.name}'s key`}
          value={create.data.token}
          help="Copy it now: Archie keeps only a fingerprint and can't show it again. Send it as Authorization: Bearer <key>."
        />
        <div className="mt-3 flex justify-end">
          <Button size="sm" variant="primary" onClick={onDone}>
            Done
          </Button>
        </div>
      </div>
    );
  return (
    <form
      className="space-y-3 px-4 py-3"
      onSubmit={(event) => {
        event.preventDefault();
        create.mutate();
      }}
    >
      <Field
        label="Name"
        placeholder="Website widget, CI pipeline…"
        required
        value={name}
        onChange={(e) => setName(e.target.value)}
      />
      <div>
        <label htmlFor="key-chatbot" className="block px-1 text-sm font-medium text-slate">
          Chatbot
        </label>
        <Select id="key-chatbot" className="mt-1 h-9" value={kbId} onChange={(e) => setKbId(e.target.value)}>
          <option value="">Every chatbot</option>
          {kbs.data?.map((kb) => (
            <option key={kb.id} value={kb.id}>
              {kb.name}
            </option>
          ))}
        </Select>
      </div>
      <div className="flex items-center justify-between gap-3 px-1">
        <span className="text-sm font-medium text-slate">Can</span>
        <Segmented
          label="What the key can do"
          value={scope}
          onChange={setScope}
          options={[
            { value: "ask", label: SCOPES.ask },
            { value: "manage", label: SCOPES.manage },
          ]}
        />
      </div>
      <p className="px-1 text-xs text-slate">
        {scope === "ask"
          ? "Asks questions and rates answers, like someone who can ask."
          : "Also changes settings, uploads files and builds, like an editor. Never people, keys or deleting."}{" "}
        {kbId ? "Only this chatbot: other chatbots and Archie-wide lists stay out of reach." : ""}
      </p>
      <ErrorNote error={create.error} />
      <div className="flex justify-end gap-2">
        <Button size="sm" onClick={onDone}>
          Cancel
        </Button>
        <Button size="sm" variant="primary" type="submit" busy={create.isPending}>
          Create Key
        </Button>
      </div>
    </form>
  );
}

/** Preferences > API keys: keys for scripts and other apps, each limited to a chatbot and to asking or managing. */
export function KeysGroup() {
  const me = useMe();
  const owner = can(me.data, "owner");
  const keys = useQuery({ queryKey: ["api-keys"], queryFn: api.apiKeys, enabled: owner });
  const kbs = useQuery({ queryKey: ["kbs"], queryFn: api.listKbs, enabled: owner });
  const [adding, setAdding] = useState(false);
  if (!owner) return null;
  if (!keys.data) return keys.error ? <ErrorNote error={keys.error} /> : null;
  const names = new Map((kbs.data ?? []).map((kb) => [kb.id, kb.name]));
  return (
    <Group
      title="API keys"
      footer="For scripts, websites and other apps. Each key works only where you allow it; revoke it to stop it at once."
    >
      {keys.data.map((apiKey) => (
        <KeyRow
          key={apiKey.id}
          apiKey={apiKey}
          chatbot={apiKey.kb_id ? (names.get(apiKey.kb_id) ?? "One chatbot") : "Every chatbot"}
        />
      ))}
      {adding ? (
        <NewKey onDone={() => setAdding(false)} />
      ) : (
        <Row label="">
          <Button size="sm" onClick={() => setAdding(true)}>
            New Key…
          </Button>
        </Row>
      )}
    </Group>
  );
}
