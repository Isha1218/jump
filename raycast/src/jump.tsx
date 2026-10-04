import { Action, ActionPanel, closeMainWindow, getPreferenceValues, Icon, LaunchProps, List, open } from "@raycast/api";
import { useExec } from "@raycast/utils";
import { execFile } from "child_process";
import { homedir } from "os";

type Result = { page_id: number; url: string; label: string; kind: string; probability: number | null };

export default function Command(props: LaunchProps<{ arguments: Arguments.Jump }>) {
  const { jumpPath } = getPreferenceValues<Preferences>();
  const bin = (jumpPath || "~/projects/jump/main/.venv/bin/jump").replace(/^~/, homedir());
  // API keys are read by the jump CLI from ~/.jump/keys
  const { isLoading, data, error } = useExec(bin, ["search", props.arguments.query, "--json"], {
    parseOutput: ({ stdout }) => JSON.parse(stdout) as Result[],
  });

  return (
    <List isLoading={isLoading} navigationTitle={props.arguments.query}>
      {error ? (
        <List.EmptyView icon={Icon.Warning} title="Search failed" description={error.message} />
      ) : (
        <List.EmptyView
          title="No confident match"
          description="Nothing scored high enough. Jump only knows pages you've visited while it was monitoring."
        />
      )}
      {(data ?? []).map((r) => (
        <List.Item
          key={r.url}
          title={r.label}
          subtitle={new URL(r.url).hostname}
          accessories={r.probability === null ? [] : [{ text: r.probability.toFixed(2) }]}
          actions={
            <ActionPanel>
              <Action
                title="Open in Chrome"
                icon={Icon.Globe}
                onAction={async () => {
                  // remember the pick so this page ranks higher next time
                  execFile(bin, ["pick", String(r.page_id), "--query", props.arguments.query]);
                  await open(r.url, "Google Chrome");
                  await closeMainWindow();
                }}
              />
              <Action.CopyToClipboard title="Copy URL" content={r.url} />
            </ActionPanel>
          }
        />
      ))}
    </List>
  );
}
