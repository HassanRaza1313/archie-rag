import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { Button, ErrorNote, Group, Row, Select, StatusDot } from "../../components/ui";
import { api, type SetupModels } from "../../lib/api";

export type ModelChoice = { embedding?: { provider: string; model: string }; generation?: { model: string } };

/** What a new chatbot will run on, checked live, with the one command that fixes anything missing. */
export function ModelCheck({
  preset,
  choice,
  onChoice,
}: {
  preset: string;
  choice: ModelChoice;
  onChoice: (choice: ModelChoice) => void;
}) {
  const check = useQuery({ queryKey: ["setup-models", preset], queryFn: () => api.setupModels(preset) });
  const data: SetupModels | undefined = check.data;

  // The usual search model is missing but Archie's built-in one works: use it, no download needed.
  // biome-ignore lint/correctness/useExhaustiveDependencies: react to each new check only
  useEffect(() => {
    if (data?.fallback_embedding) onChoice({ ...choice, embedding: data.fallback_embedding });
    else if (data?.embedding.ok && choice.embedding) onChoice({ ...choice, embedding: undefined });
  }, [data]);

  if (check.isLoading) return null;
  if (check.error || !data) return <ErrorNote error={check.error} />;

  const searchOk = data.embedding.ok || Boolean(data.fallback_embedding);
  const answerModel = choice.generation?.model ?? data.generation.model;
  const answerOk = data.generation.ok || Boolean(choice.generation);
  const others = data.installed.filter((model) => !model.includes("embed"));

  return (
    <Group
      title="Runs on"
      className="mt-6"
      footer={
        !(searchOk && answerOk) && (
          <span>
            Archie uses Ollama to run models on this computer.{" "}
            <a
              href="https://ollama.com/download"
              target="_blank"
              rel="noreferrer"
              className="text-accent hover:underline"
            >
              Get Ollama
            </a>
            , then run the command shown and check again.
          </span>
        )
      }
    >
      <Row
        label="Finding passages"
        detail={
          data.embedding.ok
            ? `${data.embedding.model}, ready`
            : data.fallback_embedding
              ? "Archie's built-in search model, ready. Nothing to download."
              : `Needs ${data.embedding.model}: ollama pull ${data.embedding.model}`
        }
      >
        <StatusDot status={searchOk ? "ready" : "missing"} />
      </Row>
      <Row
        label="Writing answers"
        detail={
          answerOk
            ? `${answerModel}, ready`
            : `Needs ${data.generation.model}: ollama pull ${data.generation.model}. You can create the chatbot now; answers start once the model is there.`
        }
      >
        {!data.generation.ok && others.length > 0 && (
          <Select
            aria-label="Model for answers"
            value={choice.generation?.model ?? ""}
            onChange={(event) =>
              onChoice({ ...choice, generation: event.target.value ? { model: event.target.value } : undefined })
            }
            className="w-40"
          >
            <option value="">Use another…</option>
            {others.map((model) => (
              <option key={model}>{model}</option>
            ))}
          </Select>
        )}
        <StatusDot status={answerOk ? "ready" : "missing"} />
      </Row>
      {!(searchOk && answerOk) && (
        <Row label="">
          <Button size="sm" busy={check.isFetching} onClick={() => void check.refetch()}>
            Check Again
          </Button>
        </Row>
      )}
    </Group>
  );
}
