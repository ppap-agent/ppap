#!/bin/sh
# Build and upload the AI node card. Its code lives in the plugin project (src/features/ai_node) so the
# card and the plugin share one implementation; only the app config differs. The plugin's own config is
# put back afterwards, whatever happens.
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
plugin="$here/../meego-plugin"
[ -f "$here/plugin.config.json" ] || { echo "copy plugin.config.example.json to plugin.config.json and fill it first" >&2; exit 1; }
cp "$plugin/plugin.config.json" "$plugin/.plugin.config.json.keep"
trap 'mv "$plugin/.plugin.config.json.keep" "$plugin/plugin.config.json"' EXIT
cp "$here/plugin.config.json" "$plugin/plugin.config.json"
cd "$plugin"
npx -p @lark-project/cli@2.8.6 lpm release --source-type remote
