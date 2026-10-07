import { Component } from 'react';
import type { ReactNode } from 'react';

export class BlockBoundary extends Component<{ children: ReactNode; fallback: string }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    return this.state.failed
      ? <div className="resource-fallback" role="alert"><strong>这段内容暂时无法渲染</strong><p>{this.props.fallback}</p></div>
      : this.props.children;
  }
}
