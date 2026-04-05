import { useState, useRef, useCallback } from "react";
import { Send } from "lucide-react";

interface Props {
  onSend: (message: string) => void;
  disabled?: boolean;
}

export function ChatInput({ onSend, disabled }: Props) {
  const [value, setValue] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const handleSend = useCallback(() => {
    const trimmed = value.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setValue("");
    // Reset textarea height
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
  }, [value, disabled, onSend]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleInput = () => {
    const el = textareaRef.current;
    if (el) {
      el.style.height = "auto";
      el.style.height = Math.min(el.scrollHeight, 200) + "px";
    }
  };

  return (
    <div className="border-t border-gray-700/50 bg-gray-800/50 px-4 py-3">
      <div className="flex items-end gap-2 max-w-4xl mx-auto">
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => {
            setValue(e.target.value);
            handleInput();
          }}
          onKeyDown={handleKeyDown}
          placeholder={
            disabled
              ? "Connecting to session..."
              : "Describe what you want to build or fix..."
          }
          disabled={disabled}
          rows={1}
          className="flex-1 resize-none bg-gray-900 border border-gray-700 rounded-lg px-3.5 py-2.5
                     text-sm text-gray-100 placeholder-gray-500
                     focus:outline-none focus:ring-1 focus:ring-blue-500/50 focus:border-blue-500/50
                     disabled:opacity-50 disabled:cursor-not-allowed
                     transition-colors"
        />
        <button
          onClick={handleSend}
          disabled={disabled || !value.trim()}
          className="p-2.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-white
                     disabled:opacity-30 disabled:cursor-not-allowed
                     transition-colors flex-shrink-0"
        >
          <Send className="w-4 h-4" />
        </button>
      </div>
      <p className="text-[10px] text-gray-600 text-center mt-1.5">
        Press Enter to send, Shift+Enter for new line
      </p>
    </div>
  );
}
