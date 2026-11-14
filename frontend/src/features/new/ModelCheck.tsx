import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { Button, ErrorNote, Group, Row, Select, StatusDot } from "../../components/ui";
import { api, type SetupModels } from "../../lib/api";

export type ModelChoice = {
  embedding?: { provider: string; model: string };
  generation?: { provider: string; model: string };
};

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
  const picked = data.answer_options.find(
    (option) => option.provider === choice.generation?.provider && option.model === choice.generation?.model,
  );
  const answerModel = picked
    ? `${picked.model} on ${picked.name}`
    : (choice.generation?.model ?? data.generation.model);
  const answerOk = data.generation.ok || Boolean(choice.generation);
  // Ollama's other chat models, then the models of providers connected in Preferences > Models.
  const options = [
    ...(data.generation.ok
      ? []
      : data.installed
          .filter((model) => !model.includes("embed"))
          .map((model) => ({ provider: "ollama", name: "Ollama", model }))),
    ...data.answer_options,
  ];
  const chosen = choice.generation ? `${choice.generation.provider}|${choice.generation.model}` : "";

  return (
    <Group
      title="Runs on"
      className="mt-6"
      footer={
        !(searchOk && answerOk) && (
          <span>
            Archie can run models on this computer with Ollama, or use Claude, OpenAI, Gemini or your own server
            (Preferences, Models).{" "}
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
            : options.length > 0
              ? `Choose a model, or install ${data.generation.model} with: ollama pull ${data.generation.model}`
              : `Needs ${data.generation.model}: ollama pull ${data.generation.model}. You can create the chatbot now; answers start once the model is there.`
        }
      >
        {options.length > 0 && (
          <Select
            aria-label="Model for answers"
            value={chosen}
            onChange={(event) => {
              const [provider = "ollama", ...model] = event.target.value.split("|");
              onChoice({
                ...choice,
                generation: event.target.value ? { provider, model: model.join("|") } : undefined,
              });
            }}
            className="w-56"
          >
            <option value="">{data.generation.ok ? "Use another…" : "Choose a model…"}</option>
            {options.map((option) => (
              <option key={`${option.provider}|${option.model}`} value={`${option.provider}|${option.model}`}>
                {option.provider === "ollama" ? option.model : `${option.model} (${option.name})`}
              </option>
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
