interface Props {
  text: string;
}

export function StepIndicator({ text }: Props) {
  return (
    <div className="flex items-center gap-2 text-sm text-gray-400">
      <span className="relative flex h-2.5 w-2.5">
        <span className="animate-pulse-dot absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75" />
        <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-blue-500" />
      </span>
      <span className="text-xs">{text}</span>
    </div>
  );
}
