import { useEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, Check, Copy, ExternalLink, Loader2, Sparkles, X } from "lucide-react";
import type { UiCoreStatus } from "../../protocol/generated-types";
import type { UiAuthState } from "../../protocol/types";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "../common/components/Tooltip";
import { logoDarkUrl, logoLightUrl } from "../common/render";
import { rpcClient } from "../common/webviewRpcClient";
import "./SidebarHeader.css";

interface SidebarHeaderProps {
  authState: UiAuthState;
  coreStatus: UiCoreStatus;
  hasExtensionError: boolean;
  connected: boolean;
}

export function SidebarHeader({
  authState,
  coreStatus,
  hasExtensionError,
  connected,
}: SidebarHeaderProps) {
  const [accountMenuOpen, setAccountMenuOpen] = useState(false);
  const [balance, setBalance] = useState<string | null>(null);
  const [signInUrl, setSignInUrl] = useState<string | null>(null);
  const [signInPending, setSignInPending] = useState(false);
  const [copied, setCopied] = useState(false);
  const accountMenuRef = useRef<HTMLDivElement | null>(null);
  const accountTriggerRef = useRef<HTMLButtonElement | null>(null);
  const fullName = authState.user?.name?.trim() || "Signed in";
  const firstName = fullName.split(" ")[0];
  const imageUrl = authState.user?.imageUrl;

  useEffect(() => {
    if (!accountMenuOpen) return;
    setBalance(null);
    rpcClient?.requestAction("authGetBalance").then((result) => {
      const usd = (result as { credit_balance_usd?: number })?.credit_balance_usd;
      if (typeof usd === "number") {
        setBalance(`$${usd.toFixed(2)}`);
      }
    }).catch(() => {});
  }, [accountMenuOpen]);
  const health = useMemo(() => {
    if (!connected) {
      return {
        tone: "error",
        label: "Disconnected",
        icon: <X size={12} />,
      };
    }
    if (hasExtensionError) {
      return {
        tone: "warning",
        label: "Degraded",
        icon: <AlertCircle size={12} />,
      };
    }
    if (!coreStatus.version) {
      return {
        tone: "loading",
        label: "Starting",
        icon: <Loader2 size={12} className="spin" />,
      };
    }
    return {
      tone: "success",
      label: "Healthy",
      icon: <Check size={12} />,
    };
  }, [connected, hasExtensionError, coreStatus.version]);

  useEffect(() => {
    if (!accountMenuOpen) {
      return;
    }

    const handlePointerDown = (event: MouseEvent) => {
      const target = event.target as Node | null;
      if (!target) {
        return;
      }
      if (accountMenuRef.current?.contains(target) || accountTriggerRef.current?.contains(target)) {
        return;
      }
      setAccountMenuOpen(false);
    };

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setAccountMenuOpen(false);
      }
    };

    window.addEventListener("mousedown", handlePointerDown);
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("mousedown", handlePointerDown);
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [accountMenuOpen]);

  useEffect(() => {
    if (!authState.isAuthenticated) {
      setAccountMenuOpen(false);
    } else {
      setSignInUrl(null);
      setSignInPending(false);
    }
  }, [authState.isAuthenticated]);

  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 1500);
    return () => clearTimeout(timer);
  }, [copied]);

  return (
    <>
    <div className="sidebar-header">
      <a href="https://atopile.io" title="atopile.io">
        {logoDarkUrl && <img src={logoDarkUrl} alt="atopile" className="sidebar-logo sidebar-logo-dark" />}
        {logoLightUrl && <img src={logoLightUrl} alt="atopile" className="sidebar-logo sidebar-logo-light" />}
      </a>
      {coreStatus.version ? <span className="version-badge">v{coreStatus.version}</span> : null}
      {health.tone !== "success" && (
        <span className={`sidebar-health sidebar-health-${health.tone}`} title={health.label}>
          {health.icon}
          <span>{health.label}</span>
        </span>
      )}
      <TooltipProvider delayDuration={200}>
        <div className="sidebar-header-actions">
          <Tooltip>
            <TooltipTrigger>
              <button
                type="button"
                className="sidebar-header-icon-btn"
                aria-label="Open Agent"
                onClick={() => {
                  void rpcClient?.requestAction("vscode.revealAgent");
                }}
              >
                <Sparkles size={14} />
              </button>
            </TooltipTrigger>
            <TooltipContent side="bottom">Open Agent</TooltipContent>
          </Tooltip>

          {!authState.isAuthenticated && (
            <button
              type="button"
              className="sidebar-signin-btn"
              title="Sign in"
              disabled={signInPending}
              onClick={() => {
                if (signInPending) return;
                void (async () => {
                  const client = rpcClient;
                  if (!client) return;
                  setSignInPending(true);
                  setCopied(false);
                  try {
                    const { url, pendingId } = await client.requestAction<{
                      url: string;
                      pendingId: string;
                    }>("authSignIn");
                    setSignInUrl(url);
                    await client.requestAction("vscode.openExternal", { url });
                    await client.requestAction("authSignInComplete", { pendingId });
                  } catch (err) {
                    console.error("sign-in failed", err);
                  } finally {
                    setSignInPending(false);
                    setSignInUrl(null);
                  }
                })();
              }}
            >
              {signInPending ? (
                <>
                  <Loader2 size={12} className="spin" />
                  <span>Signing in…</span>
                </>
              ) : (
                "Sign in"
              )}
            </button>
          )}

          {authState.isAuthenticated && (
            <div className="sidebar-account" ref={accountMenuRef}>
              <button
                ref={accountTriggerRef}
                type="button"
                className="sidebar-account-trigger"
                title="Account"
                aria-haspopup="menu"
                aria-expanded={accountMenuOpen}
                onClick={() => {
                  setAccountMenuOpen((open) => !open);
                }}
              >
                <span className="sidebar-user-name">{firstName}</span>
                {imageUrl ? (
                  <img src={imageUrl} alt={fullName} className="sidebar-avatar" />
                ) : (
                  <span className="sidebar-avatar sidebar-avatar-initials">
                    {fullName
                      .split(" ")
                      .map((segment) => segment[0])
                      .join("")
                      .slice(0, 2)
                      .toUpperCase() || "?"}
                  </span>
                )}
              </button>

              {accountMenuOpen && (
                <div className="sidebar-account-menu" role="menu" aria-label="Account">
                  <div className="sidebar-account-balance">
                    Credit: {balance ?? <Loader2 size={11} className="spin" />}
                  </div>
                  <button
                    type="button"
                    className="sidebar-account-menu-item"
                    role="menuitem"
                    onClick={() => {
                      setAccountMenuOpen(false);
                      void rpcClient?.requestAction("vscode.openPanel", { panelId: "panel-settings" });
                    }}
                  >
                    Settings
                  </button>
                  <button
                    type="button"
                    className="sidebar-account-menu-item"
                    role="menuitem"
                    onClick={() => {
                      setAccountMenuOpen(false);
                      void rpcClient?.requestAction("vscode.authOpenProfile");
                    }}
                  >
                    Open account on web
                  </button>
                  <button
                    type="button"
                    className="sidebar-account-menu-item"
                    role="menuitem"
                    onClick={() => {
                      setAccountMenuOpen(false);
                      void rpcClient?.requestAction("authSignOut");
                    }}
                  >
                    Log out
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      </TooltipProvider>
    </div>

    {signInPending && (
      <div className="sidebar-signin-banner" role="dialog" aria-label="Sign in fallback">
        <div className="sidebar-signin-banner-title">Complete sign-in in your browser</div>
        <div className="sidebar-signin-banner-hint">
          Didn't open automatically? Use this link:
        </div>
        <div className="sidebar-signin-banner-url" title={signInUrl ?? undefined}>
          {signInUrl ?? "Preparing link…"}
        </div>
        <div className="sidebar-signin-banner-actions">
          <button
            type="button"
            className="sidebar-signin-banner-btn"
            disabled={!signInUrl}
            onClick={() => {
              if (!signInUrl) return;
              void navigator.clipboard
                ?.writeText(signInUrl)
                .then(() => setCopied(true))
                .catch(() => {});
            }}
          >
            <Copy size={12} />
            <span>{copied ? "Copied" : "Copy link"}</span>
          </button>
          <a
            className="sidebar-signin-banner-btn sidebar-signin-banner-btn-primary"
            href={signInUrl ?? "#"}
            aria-disabled={!signInUrl}
            target="_blank"
            rel="noreferrer noopener"
            onClick={(event) => {
              if (!signInUrl) event.preventDefault();
            }}
          >
            <ExternalLink size={12} />
            <span>Open</span>
          </a>
        </div>
      </div>
    )}
    </>
  );
}
