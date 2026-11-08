import { useQuery } from "@tanstack/react-query";
import { Check, ChevronLeft, ChevronRight, Folder } from "lucide-react";
import { useEffect, useState } from "react";
import { cn, ErrorNote, Skeleton } from "../../components/ui";
import { api } from "../../lib/api";
import { formatNumber } from "../../lib/format";

export function fileCount(files: number): string {
  if (files >= 1000) return "1,000+ files";
  if (files === 0) return "No documents";
  return `${formatNumber(files)} file${files === 1 ? "" : "s"}`;
}

const baseName = (path: string) => path.split(/[\\/]/).filter(Boolean).pop() ?? path;

/** Browse the folders Archie may read, like an Open panel: click to choose, open with the chevron or a double-click. */
export function FolderPicker({ value, onChange }: { value: string; onChange: (path: string) => void }) {
  const [at, setAt] = useState<string | undefined>(undefined);
  const [manyRoots, setManyRoots] = useState(false);
  const listing = useQuery({ queryKey: ["folders", at ?? ""], queryFn: () => api.folders(at) });
  const data = listing.data;

  // biome-ignore lint/correctness/useExhaustiveDependencies: react to each new listing only
  useEffect(() => {
    if (!data) return;
    if (data.path === null) setManyRoots(data.folders.length > 1);
    if (data.path === null && data.folders.length === 1) setAt(data.folders[0]?.path);
    else if (!value && data.path) onChange(data.folders.find((f) => f.name === "sample")?.path ?? data.path);
  }, [data]);

  const rows = data?.path ? [{ name: `All of ${baseName(data.path)}`, path: data.path, files: data.files }] : [];
  const choose = (path: string, files: number) => files > 0 && onChange(path);

  return (
    <div className="mt-5 overflow-hidden rounded-[var(--radius-group)] shadow-[0_0_0_0.5px_var(--color-rule-strong)]">
      <div className="flex h-10 items-center gap-1 border-b border-rule bg-wall px-2">
        <button
          type="button"
          aria-label="Back"
          disabled={!data?.path || (!data.parent && !manyRoots)}
          onClick={() => setAt(data?.parent ?? undefined)}
          className="press grid size-7 cursor-default place-items-center rounded-md text-slate hover:bg-plinth disabled:opacity-30"
        >
          <ChevronLeft className="size-4" />
        </button>
        <span className="truncate text-sm font-medium">{data?.path ? baseName(data.path) : "Folders"}</span>
      </div>
      <div className="max-h-64 overflow-y-auto bg-panel">
        {listing.isLoading && (
          <div className="p-4">
            <Skeleton lines={2} />
          </div>
        )}
        {listing.error && (
          <div className="p-4">
            <ErrorNote error={listing.error} />
          </div>
        )}
        <ul aria-label="Folders" className="divide-y divide-rule">
          {[...rows, ...(data?.folders ?? [])].map((folder, index) => {
            const chosen = value === folder.path;
            const isCurrent = index === 0 && rows.length > 0;
            return (
              <li key={folder.path} className={cn("flex items-center", chosen && "bg-accent-soft")}>
                <button
                  type="button"
                  aria-pressed={chosen}
                  disabled={folder.files === 0}
                  onClick={() => choose(folder.path, folder.files)}
                  onDoubleClick={() => !isCurrent && setAt(folder.path)}
                  className="flex min-w-0 flex-1 cursor-default items-center gap-3 px-3.5 py-2.5 text-left disabled:opacity-50"
                >
                  <Folder className="size-[18px] shrink-0 fill-accent-soft text-accent" aria-hidden />
                  <span className="min-w-0 flex-1 truncate text-base">{folder.name}</span>
                  <span className="shrink-0 text-sm text-slate tabular-nums">{fileCount(folder.files)}</span>
                  <Check
                    aria-hidden
                    strokeWidth={2.5}
                    className={cn("size-4 shrink-0 text-accent", chosen ? "opacity-100" : "opacity-0")}
                  />
                </button>
                {!isCurrent && (
                  <button
                    type="button"
                    aria-label={`Open ${folder.name}`}
                    onClick={() => setAt(folder.path)}
                    className="grid h-11 w-9 shrink-0 cursor-default place-items-center text-faint hover:text-ink"
                  >
                    <ChevronRight className="size-4" />
                  </button>
                )}
              </li>
            );
          })}
        </ul>
        {data && data.folders.length === 0 && data.path && (
          <p className="px-4 py-3 text-sm text-slate">No folders inside.</p>
        )}
      </div>
    </div>
  );
}
