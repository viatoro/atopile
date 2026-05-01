import React from "react";
import { render, logoUrl } from "../common/render";
import { rpcClient } from "../common/webviewRpcClient";
import "./welcome.css";

const QUICKSTART_URL =
  "https://docs.atopile.io/atopile-0.14.x/quickstart/1-installation";

function openExternal(url: string) {
  rpcClient?.sendAction("vscode.openExternal", { url });
}

function ExternalLink({
  href,
  className,
  children,
}: {
  href: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <a
      className={className}
      onClick={(e) => {
        e.preventDefault();
        openExternal(href);
      }}
    >
      {children}
    </a>
  );
}

function App() {
  return (
    <>
      <div className="ephemeral-banner">
        <strong>Ephemeral playground</strong> &mdash; this is a temporary
        sandbox. Nothing is saved &mdash; your changes are lost when the session
        ends.
      </div>

      <div className="hero">
        <img className="hero-logo" src={logoUrl} alt="atopile" />
        <h1>Welcome to atopile</h1>
        <p className="tagline">Design electronics with code</p>
      </div>

      <div className="content">
        <h2>Getting started</h2>
        <div className="steps">
          <Step num={1} title="Explore the editor">
            The entry <code>.ato</code> file is open beside this tab &mdash; it
            defines a simple circuit with real components.
          </Step>
          <Step num={2} title="See the layout">
            The PCB layout viewer opens automatically. Edit the code and watch
            the board update in real-time.
          </Step>
          <Step num={3} title="Try something">
            Add a component, change a value, connect a signal &mdash; the
            toolchain rebuilds instantly.
          </Step>
        </div>

        <h2>Learn more</h2>
        <div className="docs-links">
          <ExternalLink className="docs-link" href="https://docs.atopile.io">
            <div className="docs-link-icon">&#128218;</div>
            <div className="docs-link-label">Documentation</div>
          </ExternalLink>
          <ExternalLink
            className="docs-link"
            href="https://github.com/atopile/atopile"
          >
            <div className="docs-link-icon">&#128187;</div>
            <div className="docs-link-label">Source code</div>
          </ExternalLink>
          <ExternalLink
            className="docs-link"
            href="https://discord.gg/nr5V3cVPBb"
          >
            <div className="docs-link-icon">&#128172;</div>
            <div className="docs-link-label">Community</div>
          </ExternalLink>
        </div>

        <div className="cta-card">
          <h3>Ready to build for real?</h3>
          <p>
            Set up atopile locally in about 5 minutes.
            <br />
            Full toolchain, persistent projects, KiCad export.
          </p>
          <ExternalLink className="cta-btn" href={QUICKSTART_URL}>
            Get started
          </ExternalLink>
        </div>
      </div>
    </>
  );
}

function Step({
  num,
  title,
  children,
}: {
  num: number;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="step">
      <div className="step-num">{num}</div>
      <div className="step-text">
        <div className="step-title">{title}</div>
        <div className="step-desc">{children}</div>
      </div>
    </div>
  );
}

render(App);
