import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Button, ErrorNote, Field, Group, Row, Select, StatusDot } from "../../components/ui";
import { api, type Connection, type ConnectionKind } from "../../lib/api";
import { can, useMe } from "../account/auth";

const KINDS: Record<ConnectionKind, { label: string; key?: string; address?: string; where: string }> = {
  anthropic: { label: "Claude", key: "sk-ant-…", where: "Create a key at console.anthropic.com." },
  openai: { label: "OpenAI", key: "sk-…", where: "Create a key at platform.openai.com." },
  gemini: { label: "Gemini", key: "AIza…", where: "Create a key at aistudio.google.com." },
  ollama: { label: "Ollama on another computer", address: "http://gpu-box:11434", where: "Ollama's address." },
  compatible: {
    label: "Other server (vLLM, LM Studio, llama.cpp…)",
    address: "http://gpu-box:8000/v1",
    where: "Any server that speaks the OpenAI API. Add a key only if it asks for one.",
  },
};

function uses(connection: Connection): string {
  if (connection.chat && connection.embeddings) return "Answers and search";
  return connection.chat ? "Answers" : "Search";
}

function ConnectionRow({ connection, owner }: { connection: Connection; owner: boolean }) {
  const queryClient = useQueryClient();
  const remove = useMutation({
    mutationFn: () => api.removeConnection(connection.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["connections"] }),
  });
  // A live check, shared with the model suggestions in Setup.
  const live = useQuery({
    queryKey: ["models", connection.id],
    queryFn: () => api.models(connection.id),
    enabled: connection.readable,
    staleTime: 60_000,
    retry: false,
  });
  const detail = !connection.readable
    ? "Key can't be read on this computer. Remove it and add it again."
    : live.error
      ? `Not reachable${connection.base_url ? ` at ${connection.base_url}` : ""}`
      : [uses(connection), connection.key_hint && `key ${connection.key_hint}`].filter(Boolean).join(" · ");
  const status = !connection.readable || live.error ? "missing" : live.isSuccess ? "ready" : null;
  return (
    <>
      <Row label={connection.name} detail={detail}>
        {!connection.builtin && owner && (
          <Button size="sm" busy={remove.isPending} onClick={() => remove.mutate()}>
            Remove
          </Button>
        )}
        <StatusDot status={status} />
      </Row>
      {remove.error && (
        <div className="px-4 pb-3">
          <ErrorNote error={remove.error} />
        </div>
      )}
    </>
  );
}

function AddProvider({ onDone }: { onDone: (message: string) => void }) {
  const queryClient = useQueryClient();
  const [kind, setKind] = useState<ConnectionKind>("anthropic");
  const [address, setAddress] = useState("");
  const [key, setKey] = useState("");
  const info = KINDS[kind];
  const add = useMutation({
    mutationFn: () => api.addConnection({ kind, base_url: address, api_key: key }),
    onSuccess: ({ connection, models }) => {
      void queryClient.invalidateQueries({ queryKey: ["connections"] });
      void queryClient.invalidateQueries({ queryKey: ["setup-models"] });
      onDone(`${connection.name} is connected: ${models.length} ${models.length === 1 ? "model" : "models"} found.`);
    },
  });
  return (
    <form
      className="space-y-3 px-4 py-3"
      onSubmit={(event) => {
        event.preventDefault();
        add.mutate();
      }}
    >
      <div>
        <label htmlFor="provider-kind" className="block px-1 text-sm font-medium text-slate">
          Provider
        </label>
        <Select
          id="provider-kind"
          className="mt-1 h-9"
          value={kind}
          onChange={(event) => {
            setKind(event.target.value as ConnectionKind);
            add.reset();
          }}
        >
          {(Object.keys(KINDS) as ConnectionKind[]).map((value) => (
            <option key={value} value={value}>
              {KINDS[value].label}
            </option>
          ))}
        </Select>
      </div>
      {info.address && (
        <Field
          label="Address"
          placeholder={info.address}
          required
          value={address}
          onChange={(e) => setAddress(e.target.value)}
        />
      )}
      {(info.key || kind === "compatible") && (
        <Field
          label={kind === "compatible" ? "API key (optional)" : "API key"}
          type="password"
          autoComplete="off"
          placeholder={info.key}
          required={Boolean(info.key)}
          value={key}
          onChange={(e) => setKey(e.target.value)}
        />
      )}
      <p className="px-1 text-xs text-slate">{info.where} Archie checks it before saving.</p>
      <ErrorNote error={add.error} />
      <div className="flex justify-end gap-2">
        <Button size="sm" onClick={() => onDone("")}>
          Cancel
        </Button>
        <Button size="sm" variant="primary" type="submit" busy={add.isPending}>
          Connect
        </Button>
      </div>
    </form>
  );
}

/** Preferences > Models: what chatbots can run on, like Internet Accounts on a Mac. */
export function ModelsGroup() {
  const me = useMe();
  const owner = can(me.data, "owner");
  const connections = useQuery({
    queryKey: ["connections"],
    queryFn: api.connections,
    enabled: can(me.data, "editor"),
  });
  const [adding, setAdding] = useState(false);
  const [note, setNote] = useState("");
  if (!connections.data) return connections.error ? <ErrorNote error={connections.error} /> : null;
  return (
    <Group
      title="Models"
      footer={
        note ||
        "Keys are encrypted on the server and never shown again. Each chatbot picks its models in Setup, from these."
      }
    >
      {connections.data.map((connection) => (
        <ConnectionRow key={connection.id} connection={connection} owner={owner} />
      ))}
      {owner &&
        (adding ? (
          <AddProvider
            onDone={(message) => {
              setAdding(false);
              setNote(message);
            }}
          />
        ) : (
          <Row label="">
            <Button
              size="sm"
              onClick={() => {
                setAdding(true);
                setNote("");
              }}
            >
              Add Provider…
            </Button>
          </Row>
        ))}
    </Group>
  );
}
