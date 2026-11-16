import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Button, ErrorNote, Field, Group, Row, Select, TextArea } from "../../components/ui";
import { api, type SourceAccount, type SourceCatalog } from "../../lib/api";
import { can, useMe } from "../account/auth";

/** Where to get each kind's credentials, in one line. */
const WHERE: Record<string, string> = {
  s3: "An access key of an IAM user (or MinIO, R2 and others) that may read the buckets.",
  database: "Use a database user that can only read. Archie also refuses anything but SELECT.",
  atlassian: "Cloud: an API token from id.atlassian.com › Security. Data Center: a personal access token.",
  microsoft: "An app registration with Sites.Read.All and Files.Read.All (application), admin-consented.",
  google: "A service account's JSON key; share folders with its email address as a Viewer.",
  notion: "An internal integration's secret; share pages with it from each page's ··· › Connections.",
};

function AccountRow({ account, label, owner }: { account: SourceAccount; label: string; owner: boolean }) {
  const queryClient = useQueryClient();
  const remove = useMutation({
    mutationFn: () => api.removeSourceAccount(account.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["source-accounts"] }),
  });
  const where = account.settings.base_url || account.settings.endpoint_url || account.settings.region;
  return (
    <>
      <Row
        label={account.name}
        detail={[account.name !== label && label, where, account.hint && `secret ${account.hint}`]
          .filter(Boolean)
          .join(" · ")}
      >
        {owner && (
          <Button size="sm" busy={remove.isPending} onClick={() => remove.mutate()}>
            Remove
          </Button>
        )}
      </Row>
      {remove.error && (
        <div className="px-4 pb-3">
          <ErrorNote error={remove.error} />
        </div>
      )}
    </>
  );
}

function AddAccount({ catalog, onDone }: { catalog: SourceCatalog; onDone: (message: string) => void }) {
  const queryClient = useQueryClient();
  const kinds = Object.keys(catalog.kinds);
  const [kind, setKind] = useState(kinds[0] ?? "");
  const [name, setName] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const info = catalog.kinds[kind];
  const add = useMutation({
    mutationFn: () =>
      api.addSourceAccount({
        kind,
        name,
        settings: Object.fromEntries((info?.settings ?? []).map(([key]) => [key, values[key] ?? ""])),
        secret: Object.fromEntries((info?.secrets ?? []).map(([key]) => [key, values[key] ?? ""])),
      }),
    onSuccess: ({ account, found }) => {
      void queryClient.invalidateQueries({ queryKey: ["source-accounts"] });
      onDone(`${account.name} is connected: ${found}.`);
    },
  });
  if (!info) return null;
  const set = (key: string, value: string) => setValues((current) => ({ ...current, [key]: value }));
  return (
    <form
      className="space-y-3 px-4 py-3"
      onSubmit={(event) => {
        event.preventDefault();
        add.mutate();
      }}
    >
      <div>
        <label htmlFor="source-kind" className="block px-1 text-sm font-medium text-slate">
          Service
        </label>
        <Select
          id="source-kind"
          className="mt-1 h-9"
          value={kind}
          onChange={(event) => {
            setKind(event.target.value);
            setValues({});
            add.reset();
          }}
        >
          {kinds.map((value) => (
            <option key={value} value={value}>
              {catalog.kinds[value]?.label}
            </option>
          ))}
        </Select>
      </div>
      <Field label="Name (optional)" placeholder={info.label} value={name} onChange={(e) => setName(e.target.value)} />
      {info.settings.map(([key, label]) => (
        <Field key={key} label={label} value={values[key] ?? ""} onChange={(e) => set(key, e.target.value)} />
      ))}
      {info.secrets.map(([key, label]) =>
        key === "service_account_json" ? (
          <div key={key}>
            <label htmlFor={`secret-${key}`} className="block px-1 text-sm font-medium text-slate">
              {label}
            </label>
            <TextArea
              id={`secret-${key}`}
              rows={4}
              required
              spellCheck={false}
              className="mt-1 font-mono text-xs"
              value={values[key] ?? ""}
              onChange={(e) => set(key, e.target.value)}
            />
          </div>
        ) : (
          <Field
            key={key}
            label={label}
            type="password"
            autoComplete="off"
            required
            value={values[key] ?? ""}
            onChange={(e) => set(key, e.target.value)}
          />
        ),
      )}
      <p className="px-1 text-xs text-slate">{WHERE[kind]} Archie checks it before saving.</p>
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

/** Preferences > Sources: the accounts chatbots read services with (S3, databases, Confluence, Jira and more). */
export function SourcesGroup() {
  const me = useMe();
  const owner = can(me.data, "owner");
  const catalog = useQuery({
    queryKey: ["source-accounts"],
    queryFn: api.sourceAccounts,
    enabled: can(me.data, "editor"),
  });
  const [adding, setAdding] = useState(false);
  const [note, setNote] = useState("");
  if (!catalog.data) return catalog.error ? <ErrorNote error={catalog.error} /> : null;
  const { accounts, kinds } = catalog.data;
  if (!owner && accounts.length === 0) return null;
  return (
    <Group
      title="Sources"
      footer={
        note ||
        "Accounts for S3, databases, Confluence, Jira, SharePoint, Google Drive and Notion. Secrets are encrypted on the server and never shown again; each chatbot picks one in Settings › Data source."
      }
    >
      {accounts.map((account) => (
        <AccountRow
          key={account.id}
          account={account}
          label={kinds[account.kind]?.label ?? account.kind}
          owner={owner}
        />
      ))}
      {owner &&
        (adding ? (
          <AddAccount
            catalog={catalog.data}
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
              Add Account…
            </Button>
          </Row>
        ))}
    </Group>
  );
}
