import { render } from "../common/render";
import "./main.css";
import { WebviewRpcClient, rpcClient } from "../common/webviewRpcClient";
import {
  Field,
  FieldLabel,
  FieldDescription,
  Button,
  Checkbox,
  Badge,
  Separator,
  Table,
  TableBody,
  TableRow,
  TableCell,
} from "../common/components";

function App() {
  const coreStatus = WebviewRpcClient.useSubscribe("coreStatus");
  const settings = WebviewRpcClient.useSubscribe("extensionSettings");

  const updateSetting = (key: string, value: string | boolean) => {
    rpcClient?.sendAction("updateExtensionSetting", { key, value });
  };

  return (
    <div className="panel-centered">
      <h2>Settings</h2>

      <h3>Configuration</h3>

      <Field>
        <div style={{ display: "flex", alignItems: "center", gap: "var(--spacing-sm)" }}>
          <Checkbox
            id="enableChat"
            checked={settings.enableChat}
            onCheckedChange={(checked) => updateSetting("enableChat", checked)}
          />
          <FieldLabel htmlFor="enableChat" style={{ margin: 0 }}>
            Show agent chat
          </FieldLabel>
        </div>
      </Field>

      <Separator />

      <h3>Status</h3>

      <Table>
        <TableBody>
          <TableRow>
            <TableCell>Version</TableCell>
            <TableCell>{coreStatus.version || "..."}</TableCell>
          </TableRow>
          <TableRow>
            <TableCell>Mode</TableCell>
            <TableCell>
              <Badge variant={coreStatus.mode === "local" ? "warning" : "success"}>
                {coreStatus.mode === "local" ? "Local" : "Production"}
              </Badge>
            </TableCell>
          </TableRow>
          <TableRow>
            <TableCell>ato binary</TableCell>
            <TableCell><code>{coreStatus.atoBinary || "not resolved"}</code></TableCell>
          </TableRow>
          <TableRow>
            <TableCell>UV path</TableCell>
            <TableCell><code>{coreStatus.uvPath || (coreStatus.mode === "local" ? "N/A (local mode)" : "not resolved")}</code></TableCell>
          </TableRow>
        </TableBody>
      </Table>

      <Separator />

      <h3>Developer</h3>

      <Field>
        <FieldDescription>
          Open the developer panel for rewrite-specific diagnostics and tooling.
        </FieldDescription>
        <Button
          variant="outline"
          onClick={() => {
            void rpcClient?.requestAction("vscode.openPanel", {
              panelId: "panel-developer",
            });
          }}
        >
          Open Developer Panel
        </Button>
      </Field>
    </div>
  );
}

render(App);
