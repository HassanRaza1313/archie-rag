import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, type ReactNode, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { AppIcon } from "../../components/Layout";
import { Button, EmptyState, ErrorNote, Field, Skeleton } from "../../components/ui";
import { api, type Me, type Role, SIGNED_OUT, setApiKey } from "../../lib/api";

const RANK: Record<Role, number> = { viewer: 0, editor: 1, owner: 2 };

/** Whether someone with this identity may do what ``role`` allows (the server enforces the same rule). */
export function can(me: Me | undefined, role: Role): boolean {
  return Boolean(me?.role && RANK[me.role] >= RANK[role]);
}

export function useMe() {
  return useQuery({ queryKey: ["me"], queryFn: api.me, staleTime: 60_000, retry: false });
}

/** A centred card on the wall, like the setup assistant: for signing in and joining. */
function Card({ title, body, children }: { title: string; body?: ReactNode; children: ReactNode }) {
  return (
    <div className="flex min-h-full items-center justify-center bg-wall px-4 py-10">
      <div className="settle w-full max-w-sm rounded-[var(--radius-sheet)] bg-panel px-7 pt-9 pb-7 text-center shadow-[0_0_0_0.5px_var(--color-rule),0_10px_40px_rgb(0_0_0/0.08)]">
        <AppIcon className="mx-auto size-14" />
        <h1 className="mt-5 font-display text-2xl font-bold tracking-tight">{title}</h1>
        {body && <p className="mt-2 text-base text-slate">{body}</p>}
        {children}
      </div>
    </div>
  );
}

function useSignedIn() {
  const queryClient = useQueryClient();
  return (me: Me) => {
    queryClient.setQueryData(["me"], me);
    void queryClient.invalidateQueries({ predicate: (query) => query.queryKey[0] !== "me" });
  };
}

function SignInPage({ accounts, keyRequired }: { accounts: boolean; keyRequired: boolean }) {
  const signedIn = useSignedIn();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const signIn = useMutation({ mutationFn: () => api.signIn(email, password), onSuccess: signedIn });

  if (!accounts && !keyRequired) {
    // No accounts and no key: an install that only its own machine and network may use until it gets an owner.
    return (
      <Card
        title="Archie isn't open to the internet yet"
        body="Open it from the computer it runs on, or its local network, and create the owner account under People. Then everyone can sign in from anywhere."
      >
        <span />
      </Card>
    );
  }
  if (!accounts) {
    // Protected by the admin key and nobody has an account yet.
    return (
      <Card title="Enter the admin key" body="Whoever runs this Archie set a key. It stays in this browser.">
        <form
          className="mt-7 space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            setApiKey(password.trim());
            window.location.reload();
          }}
        >
          <Field
            label="Admin key"
            required
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <Button type="submit" variant="primary" size="lg" className="w-full">
            Continue
          </Button>
        </form>
      </Card>
    );
  }
  return (
    <Card title="Sign in to Archie">
      <form
        className="mt-7 space-y-4"
        onSubmit={(event: FormEvent) => {
          event.preventDefault();
          signIn.mutate();
        }}
      >
        <Field
          label="Email"
          required
          type="email"
          autoComplete="username"
          autoFocus
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        <Field
          label="Password"
          required
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <ErrorNote error={signIn.error} />
        <Button type="submit" variant="primary" size="lg" className="w-full" busy={signIn.isPending}>
          Sign In
        </Button>
      </form>
      <p className="mt-5 text-sm text-slate">Forgot your password? Ask an owner for a new link.</p>
    </Card>
  );
}

/** Shows the app to anyone allowed in, and the sign-in screen to everyone else. */
export function RequireSignIn({ children }: { children: ReactNode }) {
  const me = useMe();
  const queryClient = useQueryClient();
  useEffect(() => {
    const signedOut = () => void queryClient.invalidateQueries({ queryKey: ["me"] });
    window.addEventListener(SIGNED_OUT, signedOut);
    return () => window.removeEventListener(SIGNED_OUT, signedOut);
  }, [queryClient]);

  if (me.isLoading) return <div className="h-full bg-wall" />;
  if (me.data?.role) return <>{children}</>;
  return <SignInPage accounts={me.data?.accounts ?? true} keyRequired={me.data?.key_required ?? true} />;
}

/** Opened from an invitation (or a password reset link): choose a password and you're in. */
export function InvitePage() {
  const { token = "" } = useParams();
  const navigate = useNavigate();
  const signedIn = useSignedIn();
  const invitation = useQuery({ queryKey: ["invite", token], queryFn: () => api.invitation(token), retry: false });
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const join = useMutation({
    mutationFn: () => api.acceptInvite(token, name, password),
    onSuccess: (me) => {
      signedIn(me);
      navigate("/", { replace: true });
    },
  });

  if (invitation.isLoading) {
    return (
      <div className="p-10">
        <Skeleton />
      </div>
    );
  }
  if (!invitation.data) {
    return (
      <div className="flex min-h-full items-center bg-wall">
        <EmptyState title="This link no longer works">
          Invitations last a week and work once. Ask an owner for a new one.
        </EmptyState>
      </div>
    );
  }
  const joined = invitation.data.joined;
  return (
    <Card
      title={joined ? "Choose a new password" : "Join Archie"}
      body={joined ? invitation.data.email : `You're invited as ${invitation.data.email}.`}
    >
      <form
        className="mt-7 space-y-4"
        onSubmit={(event) => {
          event.preventDefault();
          join.mutate();
        }}
      >
        <input type="email" autoComplete="username" value={invitation.data.email} readOnly hidden />
        {!joined && (
          <Field
            label="Your name"
            autoComplete="name"
            required={false}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        )}
        <Field
          label="Password"
          required
          type="password"
          autoComplete="new-password"
          minLength={10}
          autoFocus={joined}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <p className="-mt-2 px-1 text-left text-xs text-slate">At least 10 characters. A short sentence works well.</p>
        <ErrorNote error={join.error} />
        <Button type="submit" variant="primary" size="lg" className="w-full" busy={join.isPending}>
          {joined ? "Save Password" : "Join"}
        </Button>
      </form>
    </Card>
  );
}
