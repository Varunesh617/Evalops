"use client";

import React, { createContext, useContext, useState, useCallback, ReactNode } from "react";

interface ToastItem {
  id: string;
  message: string;
  type: "error" | "success" | "info";
  action?: { label: string; onClick: () => void };
}

interface ErrorContextType {
  error: string | null;
  setError: (error: string | null) => void;
  toast: ToastItem | null;
  showToast: (
    message: string,
    type?: "error" | "success" | "info",
    action?: { label: string; onClick: () => void },
  ) => void;
}

const ErrorContext = createContext<ErrorContextType | undefined>(undefined);

function ErrorProvider({ children }: { children: ReactNode }) {
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<ToastItem | null>(null);

  const showToast = useCallback(
    (
      message: string,
      type: "error" | "success" | "info" = "error",
      action?: { label: string; onClick: () => void },
    ) => {
      const id = Math.random().toString(36).slice(2, 11);
      setToast({ id, message, type, action });

      setTimeout(() => {
        setToast((current) => (current?.id === id ? null : current));
      }, 5000);
    },
    [],
  );

  return (
    <ErrorContext.Provider value={{ error, setError, toast, showToast }}>
      {children}
      {toast && (
        <div className="fixed bottom-4 right-4 z-50 animate-in slide-in-from-bottom duration-300">
          <div
            className={`rounded-lg shadow-lg border p-4 max-w-md ${
              toast.type === "error"
                ? "bg-red-50 dark:bg-red-900/20 border-red-200 dark:border-red-800"
                : toast.type === "success"
                  ? "bg-emerald-50 dark:bg-emerald-900/20 border-emerald-200 dark:border-emerald-800"
                  : "bg-blue-50 dark:bg-blue-900/20 border-blue-200 dark:border-blue-800"
            }`}
          >
            <div className="flex items-start justify-between">
              <div className="flex-1">
                <p
                  className={`text-sm font-medium mb-1 ${
                    toast.type === "error"
                      ? "text-red-800 dark:text-red-200"
                      : toast.type === "success"
                        ? "text-emerald-800 dark:text-emerald-200"
                        : "text-blue-800 dark:text-blue-200"
                  }`}
                >
                  {toast.message}
                </p>
                {toast.action && (
                  <button
                    onClick={() => {
                      toast.action?.onClick();
                      setToast(null);
                    }}
                    className="text-xs font-medium underline hover:no-underline"
                  >
                    {toast.action.label}
                  </button>
                )}
              </div>
              <button
                onClick={() => setToast(null)}
                className="ml-3 text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-200"
              >
                <span className="sr-only">Close</span>×
              </button>
            </div>
          </div>
        </div>
      )}
    </ErrorContext.Provider>
  );
}

function useError() {
  const context = useContext(ErrorContext);
  if (context === undefined) {
    throw new Error("useError must be used within an ErrorProvider");
  }
  return context;
}

function ErrorBoundary({ children }: { children: ReactNode }) {
  const { error, setError } = useError();

  if (error) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-zinc-50 dark:bg-zinc-950">
        <div className="max-w-md w-full mx-4">
          <div className="bg-white dark:bg-zinc-900 rounded-lg border border-red-200 dark:border-red-800 p-8 shadow-lg">
            <div className="flex items-center justify-center w-12 h-12 rounded-full bg-red-100 dark:bg-red-900/30 mb-4">
              <span className="text-2xl">⚠️</span>
            </div>
            <h2 className="text-xl font-bold text-zinc-900 dark:text-white mb-2">
              Something went wrong
            </h2>
            <p className="text-sm text-zinc-600 dark:text-zinc-400 mb-6">{error}</p>
            <div className="flex gap-3">
              <button
                onClick={() => setError(null)}
                className="flex-1 px-4 py-2 text-sm font-medium rounded-lg bg-zinc-100 dark:bg-zinc-800 text-zinc-700 dark:text-zinc-300 hover:bg-zinc-200 dark:hover:bg-zinc-700 transition-colors"
              >
                Dismiss
              </button>
              <button
                onClick={() => {
                  setError(null);
                  window.location.reload();
                }}
                className="flex-1 px-4 py-2 text-sm font-medium rounded-lg bg-blue-600 text-white hover:bg-blue-700 transition-colors"
              >
                Retry
              </button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  return <>{children}</>;
}

export { ErrorProvider, useError, ErrorBoundary };
