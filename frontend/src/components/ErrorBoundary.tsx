import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props { children: ReactNode; }
interface State { error: Error | null; }

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // eslint-disable-next-line no-console
    console.error("UI crash:", error, info);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="app-state">
          <h1>Something went wrong</h1>
          <p>{this.state.error.message}</p>
          <pre style={{ fontSize: "11px", textAlign: "left", background: "#f7f8fc", padding: 12, borderRadius: 8, overflow: "auto" }}>
            {this.state.error.stack}
          </pre>
          <button onClick={() => { this.setState({ error: null }); window.location.reload(); }}>
            Reload
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}