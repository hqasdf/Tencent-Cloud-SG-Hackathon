import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props { children: ReactNode; }
interface State { error: Error | null; }

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(_error: Error, _info: ErrorInfo): void {
    // eslint-disable-next-line no-console
    console.error("RydeResolve UI rendering failed.");
  }

  render() {
    if (this.state.error) {
      return (
        <div className="app-state">
          <h1>Something went wrong</h1>
          <p>The page could not be displayed. Saved interviews and completed analysis stages remain on the server.</p>
          <button onClick={() => { this.setState({ error: null }); window.location.reload(); }}>
            Reload
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
