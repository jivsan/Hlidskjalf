import { Component, type ErrorInfo, type ReactNode } from "react";

/**
 * Generic error boundary. A render/lifecycle crash inside `children` is
 * contained here instead of blanking the whole SPA. Pass a `resetKey` that
 * changes on navigation so a crash on one page doesn't stick to the next.
 */
export class ErrorBoundary extends Component<
  {
    children: ReactNode;
    /** Short label for the crashed area, e.g. "page" or "faceplate". */
    label?: string;
    /** When this value changes the boundary resets and re-renders children. */
    resetKey?: unknown;
    /** Custom fallback; overrides the default card. */
    fallback?: ReactNode;
  },
  { error: Error | null }
> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(`ErrorBoundary(${this.props.label ?? "app"}):`, error, info.componentStack);
  }

  componentDidUpdate(prevProps: { resetKey?: unknown }) {
    if (this.state.error && prevProps.resetKey !== this.props.resetKey) {
      this.setState({ error: null });
    }
  }

  render() {
    if (this.state.error) {
      if (this.props.fallback) return this.props.fallback;
      return (
        <div className="card border-red/40 p-5 text-sm space-y-3" role="alert">
          <div className="text-red">
            <span className="text-muted mr-2">crash:</span>
            the {this.props.label ?? "page"} failed to render
          </div>
          <div className="text-xs text-muted font-mono break-all">
            {this.state.error.message || String(this.state.error)}
          </div>
          <div className="flex gap-2">
            <button className="btn-plain text-xs" onClick={() => this.setState({ error: null })}>
              try again
            </button>
            <button className="btn-plain text-xs" onClick={() => window.location.reload()}>
              reload panel
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
