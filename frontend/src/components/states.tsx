import { Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";

/** Shared loading / error / empty presentation so every page behaves the same way. */
export function LoadingState({ label }: { label: string }) {
  return (
    <p className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground" role="status">
      <Loader2 className="size-4 animate-spin" /> {label}
    </p>
  );
}

export function ErrorState({ title, message, onRetry }: { title: string; message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="rounded-md border border-destructive/40 bg-destructive/5 p-6 text-center text-sm">
      <p className="font-medium text-destructive">{title}</p>
      <p className="mt-1 text-muted-foreground">{message}</p>
      {onRetry && (
        <Button variant="outline" className="mt-3" onClick={onRetry}>
          Try again
        </Button>
      )}
    </div>
  );
}

export function EmptyNote({ children, testId }: { children: React.ReactNode; testId?: string }) {
  return (
    <p className="text-sm text-muted-foreground italic" data-testid={testId}>
      {children}
    </p>
  );
}
