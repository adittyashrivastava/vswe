export interface ModelOption {
  id: string;
  name: string;
  provider: string;
}

export const AVAILABLE_MODELS: ModelOption[] = [
  { id: "claude-opus-4-0-20250514", name: "Claude Opus 4", provider: "Anthropic" },
  { id: "claude-sonnet-4-20250514", name: "Claude Sonnet 4", provider: "Anthropic" },
  { id: "claude-haiku-4-20250514", name: "Claude Haiku 4", provider: "Anthropic" },
  { id: "gpt-4o", name: "GPT-4o", provider: "OpenAI" },
  { id: "gpt-4-turbo", name: "GPT-4 Turbo", provider: "OpenAI" },
  { id: "gpt-4", name: "GPT-4", provider: "OpenAI" },
  { id: "o3-mini", name: "o3-mini", provider: "OpenAI" },
];

export function getModelById(id: string): ModelOption | undefined {
  return AVAILABLE_MODELS.find((m) => m.id === id);
}

export function getModelDisplayName(id: string): string {
  const model = getModelById(id);
  return model ? model.name : id;
}
