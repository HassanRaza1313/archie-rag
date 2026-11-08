import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { UserPlus } from "lucide-react";
import { type FormEvent, useState } from "react";
import {
  Badge,
  Button,
  Dialog,
  EmptyState,
  ErrorNote,
  Field,
  Group,
  PageHeader,
  Row,
  Segmented,
  Select,
  Skeleton,
} from "../../components/ui";
import { type AuditEntry, api, type Person, type Role } from "../../lib/api";
import { relativeTime } from "../../lib/format";
import { CopyField } from "../chat/ShareSheet";
import { can, useMe } from "./auth";

export const ROLE_LABELS: Record<Role, string> = { viewer: "Can ask", editor: "Can edit", owner: "Owner" };

const ACTIONS: Record<string, string> = {
  "POST /api/kbs": "created a chatbot",
  "DELETE /api/kbs/{kb_id}": "deleted a chatbot",
  "POST /api/kbs/{kb_id}/configs": "changed settings",
  "POST /api/kbs/{kb_id}/builds": "started a build",
  "POST /api/jobs/{job_id}/cancel": "cancelled a build",
  "POST /api/kbs/{kb_id}/uploads": "uploaded files",
  "POST /api/kbs/{kb_id}/share": "changed sharing",
  "PATCH /api/kbs/{kb_id}/documents/chunks": "edited a passage",
  "DELETE /api/kbs/{kb_id}/documents/chunks": "restored a passage",
  "POST /api/users": "invited someone",
  "POST /api/users/{user_id}/invite": "made a new sign-in link",
  "PATCH /api/users/{user_id}": "changed someone's access",
  "DELETE /api/users/{user_id}": "removed someone",
};

/** An audit entry in words: "started a build", or the raw request when there is no wording for it. */
export function describeAction(entry: AuditEntry): string {
  return ACTIONS[entry.action] ?? entry.action;
}

function useInviteLink() {
  const [token, setToken] = useState<string | null>(null);
  return { link: token ? `${window.location.origin}/invite/${token}` : null, setToken };
}

function AddPerson({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("viewer");
  const invite = useInviteLink();
  const add = useMutation({
    mutationFn: () => api.addPerson(email, role),
    onSuccess: (reply) => {
      invite.setToken(reply.invite_token);
      void queryClient.invalidateQueries({ queryKey: ["people"] });
      void queryClient.invalidateQueries({ queryKey: ["audit"] });
    },
  });
  return (
    <Dialog
      open
      onClose={onClose}
      title="Add a Person"
      footer={
        invite.link ? (
          <Button variant="primary" onClick={onClose}>
            Done
          </Button>
        ) : (
          <Button type="submit" form="add-person" variant="primary" busy={add.isPending}>
            Add
          </Button>
        )
      }
    >
      {invite.link ? (
        <>
          <p className="text-base">
            Send this link to <strong className="font-semibold">{email}</strong>. It works once, for a week.
          </p>
          <CopyField label="Invitation" value={invite.link} />
        </>
      ) : (
        <form
          id="add-person"
          className="space-y-5"
          onSubmit={(event: FormEvent) => {
            event.preventDefault();
            add.mutate();
          }}
        >
          <Field
            label="Email"
            data-autofocus
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
          <Group footer="Can ask: questions only. Can edit: also create, change, build and share chatbots. Owner: also people and the change history.">
            <Row label="Access">
              <Segmented
                label="Access"
                value={role}
                onChange={setRole}
                options={(Object.keys(ROLE_LABELS) as Role[]).map((value) => ({ value, label: ROLE_LABELS[value] }))}
              />
            </Row>
          </Group>
          <ErrorNote error={add.error} />
        </form>
      )}
    </Dialog>
  );
}

function PersonRow({ person, isMe }: { person: Person; isMe: boolean }) {
  const queryClient = useQueryClient();
  const [confirmRemove, setConfirmRemove] = useState(false);
  const invite = useInviteLink();
  const refresh = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: ["people"] }),
      queryClient.invalidateQueries({ queryKey: ["audit"] }),
    ]);
  const change = useMutation({
    mutationFn: (update: { role?: Role; disabled?: boolean }) => api.changePerson(person.id, update),
    onSuccess: refresh,
  });
  const remove = useMutation({ mutationFn: () => api.removePerson(person.id), onSuccess: refresh });
  const reinvite = useMutation({
    mutationFn: () => api.reinvite(person.id),
    onSuccess: (reply) => invite.setToken(reply.invite_token),
  });
  const error = change.error ?? remove.error ?? reinvite.error;

  return (
    <div className="px-4 py-3">
      <div className="flex flex-wrap items-center gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 text-base">
            <span className="truncate">{person.name || person.email}</span>
            {isMe && <span className="text-sm text-slate">(you)</span>}
            {!person.joined && <Badge tone="accent">Invited</Badge>}
            {person.disabled ? <Badge tone="warn">Turned off</Badge> : null}
          </div>
          {person.name && <div className="truncate text-sm text-slate">{person.email}</div>}
        </div>
        <Select
          aria-label={`Access for ${person.email}`}
          value={person.role}
          disabled={change.isPending}
          onChange={(e) => change.mutate({ role: e.target.value as Role })}
          className="w-32"
        >
          {(Object.keys(ROLE_LABELS) as Role[]).map((role) => (
            <option key={role} value={role}>
              {ROLE_LABELS[role]}
            </option>
          ))}
        </Select>
      </div>
      <div className="mt-2 flex flex-wrap gap-1">
        <Button size="sm" variant="ghost" busy={reinvite.isPending} onClick={() => reinvite.mutate()}>
          {person.joined ? "New Password Link" : "New Invitation Link"}
        </Button>
        {!isMe && (
          <Button size="sm" variant="ghost" onClick={() => change.mutate({ disabled: !person.disabled })}>
            {person.disabled ? "Turn On" : "Turn Off"}
          </Button>
        )}
        {!isMe &&
          (confirmRemove ? (
            <span className="flex items-center gap-1">
              <Button size="sm" variant="danger" busy={remove.isPending} onClick={() => remove.mutate()}>
                Remove {person.name || person.email}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setConfirmRemove(false)}>
                Cancel
              </Button>
            </span>
          ) : (
            <Button size="sm" variant="ghost" onClick={() => setConfirmRemove(true)}>
              Remove…
            </Button>
          ))}
      </div>
      {invite.link && <CopyField label="Send this link. It works once, for a week." value={invite.link} />}
      {error ? (
        <div className="mt-2">
          <ErrorNote error={error} />
        </div>
      ) : null}
    </div>
  );
}

/** First account on an open install: from here on, everyone signs in. */
function TurnOnSignIn() {
  const queryClient = useQueryClient();
  const [form, setForm] = useState({ name: "", email: "", password: "" });
  const create = useMutation({
    mutationFn: () => api.createOwner(form.email, form.name, form.password),
    onSuccess: (me) => {
      queryClient.setQueryData(["me"], me);
      void queryClient.invalidateQueries();
    },
  });
  const field = (key: keyof typeof form, label: string, props: React.ComponentProps<"input">) => (
    <Field
      label={label}
      className="px-4 py-2.5"
      value={form[key]}
      onChange={(e) => setForm({ ...form, [key]: e.target.value })}
      {...props}
    />
  );
  return (
    <form
      className="max-w-xl px-5 pb-10 md:px-8"
      onSubmit={(event) => {
        event.preventDefault();
        create.mutate();
      }}
    >
      <Group
        title="Your owner account"
        footer="Once it exists, everyone signs in, and you decide who can ask, who can edit and who manages people."
      >
        {field("name", "Name", { autoComplete: "name" })}
        {field("email", "Email", { type: "email", autoComplete: "username", required: true })}
        {field("password", "Password", {
          type: "password",
          autoComplete: "new-password",
          minLength: 10,
          required: true,
        })}
      </Group>
      <div className="mt-4 flex items-center justify-end gap-3">
        <ErrorNote error={create.error} />
        <Button type="submit" variant="primary" busy={create.isPending}>
          Turn On Sign-In
        </Button>
      </div>
    </form>
  );
}

export function PeoplePage() {
  const me = useMe();
  const [adding, setAdding] = useState(false);
  const owner = can(me.data, "owner") && me.data?.via !== "open";
  const people = useQuery({ queryKey: ["people"], queryFn: api.people, enabled: owner });
  const audit = useQuery({ queryKey: ["audit"], queryFn: () => api.audit(50), enabled: owner });
  const kbs = useQuery({ queryKey: ["kbs"], queryFn: api.listKbs, enabled: owner });
  const names = new Map(kbs.data?.map((kb) => [kb.id, kb.name]));

  if (me.data?.via === "open") {
    return (
      <div className="min-h-full bg-wall">
        <PageHeader
          title="People"
          description="Right now anyone who can open this Archie can change it. Create your account to turn on sign-in."
        />
        <TurnOnSignIn />
      </div>
    );
  }
  if (!owner) {
    return <EmptyState title="Only owners manage people">Ask an owner if you need different access.</EmptyState>;
  }
  return (
    <div className="min-h-full bg-wall pb-10">
      <PageHeader
        title="People"
        description="Who can sign in to Archie, and what they can do."
        actions={
          <Button variant="primary" onClick={() => setAdding(true)}>
            <UserPlus className="size-4" aria-hidden /> Add Person
          </Button>
        }
      />
      <div className="max-w-3xl space-y-8 px-5 md:px-8">
        <Group>
          {people.isLoading ? (
            <div className="p-4">
              <Skeleton />
            </div>
          ) : (
            people.data?.map((person) => (
              <PersonRow key={person.id} person={person} isMe={person.id === me.data?.user?.id} />
            ))
          )}
        </Group>
        <Group
          title="Recent changes"
          footer="Every change to chatbots and people, newest first. Questions aren't listed here."
        >
          {audit.data?.length === 0 && <Row label="Nothing yet." />}
          {audit.data?.map((entry) => (
            <Row
              key={entry.id}
              label={
                <>
                  <span className="font-medium">{entry.actor}</span> {describeAction(entry)}
                  {entry.target && names.get(entry.target) && (
                    <span className="text-slate"> · {names.get(entry.target)}</span>
                  )}
                </>
              }
            >
              {entry.status >= 400 && <Badge tone="bad">Refused</Badge>}
              <span className="text-sm tabular-nums">{relativeTime(entry.at)}</span>
            </Row>
          ))}
        </Group>
      </div>
      {adding && <AddPerson onClose={() => setAdding(false)} />}
    </div>
  );
}
