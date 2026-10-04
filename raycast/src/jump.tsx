import { Action, ActionPanel, getPreferenceValues, Icon, LaunchProps, List } from "@raycast/api";
import { useExec } from "@raycast/utils";
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
        <List.EmptyView title="No matches" description="Jump only knows pages you've visited while it was monitoring." />
      )}
      {(data ?? []).map((r) => (
        <List.Item
          key={r.url}
          title={r.label}
          subtitle={new URL(r.url).hostname}
          accessories={r.probability === null ? [] : [{ text: r.probability.toFixed(2) }]}
          actions={
            <ActionPanel>
              <Action.Open title="Open in Chrome" target={r.url} application="Google Chrome" />
              <Action.CopyToClipboard title="Copy URL" content={r.url} />
            </ActionPanel>
          }
        />
      ))}
    </List>
  );
}
